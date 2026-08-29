# System Architecture

## Overview

Inferra is structured as a **two-plane architecture**:

- **Data plane** — vLLM running on a GPU host (RunPod NVIDIA L4), responsible for all token generation
- **Control plane** — a FastAPI API gateway running locally (or on any host with network access to the GPU), responsible for auth, routing, rate limiting, metering, and adapter lifecycle

The two planes communicate over HTTP. In local development, an SSH tunnel bridges the Mac's gateway to the RunPod pod. In production, both planes would share a private network.

---

## Component Diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║                         CLIENT TIER                                   ║
║  OpenAI SDK / curl / Any HTTP client                                  ║
║  Bearer: inf_<token>                                                   ║
╚══════════════════════════════════════╦═══════════════════════════════╝
                                       │ HTTPS :9100
╔══════════════════════════════════════▼═══════════════════════════════╗
║                      CONTROL PLANE (Mac / any host)                   ║
║  ┌─────────────────────────────────────────────────────────────────┐  ║
║  │                   FastAPI API Gateway (:9000 internal)           │  ║
║  │                                                                   │  ║
║  │  ① RequestLoggingMiddleware (request_id injection)               │  ║
║  │  ② Auth — SHA-256 hash lookup in PostgreSQL                      │  ║
║  │  ③ AdmissionControl — RPM + concurrency + quota → Redis          │  ║
║  │  ④ RoutingResolver — alias → Deployment → Worker → endpoint      │  ║
║  │  ⑤ VLLMClient — async HTTP proxy (streaming / non-streaming)     │  ║
║  │  ⑥ UsageRecorder — background task → PostgreSQL                  │  ║
║  │  ⑦ MetricsExporter — /metrics → Prometheus                       │  ║
║  └─────────────────────────────────────────────────────────────────┘  ║
║                                                                         ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  ║
║  │  PostgreSQL  │  │   Redis 7    │  │   MinIO (S3-compatible)      │  ║
║  │  (metadata)  │  │  (limits)    │  │   (LoRA adapter weights)     │  ║
║  └──────────────┘  └──────────────┘  └──────────────────────────────┘  ║
║                                                                         ║
║  ┌──────────────┐  ┌──────────────┐                                     ║
║  │  Prometheus  │  │   Grafana    │                                     ║
║  │  (:9090)     │  │   (:3000)    │                                     ║
║  └──────────────┘  └──────────────┘                                     ║
╚════════════════════════════╦════════════════════════════════════════════╝
                             │ HTTP (SSH tunnel in dev: localhost:8001 → RunPod:8000)
╔════════════════════════════▼════════════════════════════════════════════╗
║                         DATA PLANE (RunPod NVIDIA L4 24 GB)              ║
║  ┌─────────────────────────────────────────────────────────────────────┐ ║
║  │  vLLM 0.28.0  (:8000)                                               │ ║
║  │  Qwen/Qwen3-4B · BF16 · max_model_len=8192                         │ ║
║  │  --enable-lora --max-loras 4 --max-lora-rank 16                     │ ║
║  │  --enable-prefix-caching                                             │ ║
║  │  VRAM: ~19 GB used / 23 GB total at idle                            │ ║
║  └─────────────────────────────────────────────────────────────────────┘ ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

---

## Request Lifecycle

### Inference Request (streaming)

```
Client
  │
  │  POST /v1/chat/completions
  │  Authorization: Bearer inf_<token>
  │  {"model": "my-assistant", "messages": [...], "stream": true}
  │
  ▼
RequestLoggingMiddleware
  │  Injects X-Request-ID (UUID) into request.state
  │
  ▼
require_inference_key (FastAPI dependency)
  │  Extracts Bearer token
  │  SHA-256 hash → SELECT FROM api_keys WHERE key_hash = ?
  │  Validates: status=active, not expired, not is_admin
  │  Loads Organization row → AuthenticatedContext
  │  401 if key invalid, 403 if org suspended
  │
  ▼
check_admission()
  │  1. check_input_token_ceiling() — word-count estimate vs policy.max_input_tokens
  │  2. check_rpm_limit() — Redis Lua INCR + EXPIRE, atomic token bucket
  │  3. concurrency_tracker.acquire() — Redis INCR on rl:concurrent:<org_id>
  │  4. check_daily_quota() — Redis GET rl:daily_tokens:<org_id>:<date>
  │  5. check_global_queue() — Redis INCR rl:queue_depth, gate at 50
  │  429 / 503 with Retry-After if any gate fails
  │
  ▼
resolve_target()
  │  context ceiling check: estimated_prompt + max_tokens ≤ 8192
  │  SELECT ModelAlias WHERE org_id=? AND alias=?
  │    → falls back to public aliases, then direct hf_repo lookup
  │  SELECT Deployment WHERE status='running' LIMIT 1
  │  SELECT Worker WHERE id=? AND status='healthy'
  │  503 if no active deployment or unhealthy worker
  │  Returns ResolvedInferenceTarget{worker_endpoint, base_model, adapter_id, ...}
  │
  ▼
INSERT requests (status='pending')
  │
  ▼
VLLMClient.chat_completions_stream()
  │  POST worker_endpoint/v1/chat/completions (httpx async streaming)
  │  payload["model"] = adapter_runtime_name or base_model
  │
  ▼
tracked_stream() — async generator wrapper
  │  Detects first content chunk → records first_token_at (TTFT)
  │  Ingests SSE chunks → extracts usage.prompt_tokens / completion_tokens
  │  Yields each raw chunk directly to client (no buffering)
  │
  ▼
StreamingResponse(media_type="text/event-stream")
  │
  ▼  [stream completes or cancels]
  │
BackgroundTasks._finalize_usage()
  │  INSERT usage_metrics (all timing dimensions, token counts)
  │  UPDATE requests (status, http_status, first_token_at, completed_at)
  │  release_admission() — decrements Redis counters
  │  Records Prometheus counters/histograms
```

### Non-Streaming Request

Identical path through auth and admission. Instead of `StreamingResponse`, calls `client.chat_completions()` (awaited JSON), extracts usage from response body, and returns JSON directly. Timing is approximated (first_token_at = completed_at for non-streaming).

---

## Component Details

### FastAPI API Gateway

**Entry point:** `apps/api/main.py`

The gateway is a single FastAPI application registered with these routers:

| Router | Prefix | Purpose |
|--------|--------|---------|
| `health` | `/health` | vLLM connectivity check |
| `metrics` | `/metrics` | Prometheus text exposition |
| `chat` | `/v1/chat/completions` | Core inference endpoint |
| `models` | `/v1/models` | Model listing (proxied from vLLM) |
| `admin` | `/v1/api-keys`, `/v1/workers`, `/v1/deployments`, `/v1/usage` | Admin operations |
| `adapters` | `/v1/adapters`, `/v1/aliases` | LoRA adapter lifecycle |
| `admin_ui` | `/admin` | Admin HTML UI (Jinja2) |

**Startup lifecycle** (`lifespan`):
1. `setup_tracing()` — configures OpenTelemetry if `OTEL_ENABLED=true`
2. `init_db()` — runs Alembic migrations against PostgreSQL
3. `ensure_bucket_exists()` — creates MinIO bucket if missing (non-fatal if MinIO unavailable)

**Middleware:** `RequestLoggingMiddleware` injects a UUID `X-Request-ID` header and logs request start/end.

### Auth Service (`services/auth/keys.py`)

API keys are stored as SHA-256 hashes — the plaintext is never persisted. Key format: `inf_<32-byte-urlsafe-token>`.

Two key types:
- **Inference keys** (`is_admin=False`) — can call `/v1/chat/completions`, `/v1/adapters`, `/v1/usage`
- **Admin keys** (`is_admin=True`) — can manage organizations, create/revoke keys, list workers/deployments

Lookup is a single indexed query on `key_hash`. Expiry is checked in Python (not at the DB level) to avoid timezone issues.

### Routing Resolver (`services/routing/resolver.py`)

Resolves a logical model name (from the request) to a concrete `ResolvedInferenceTarget` containing:
- `worker_endpoint` — the vLLM HTTP base URL
- `base_model` — the HuggingFace repo ID to pass to vLLM
- `adapter_id` / `adapter_runtime_name` — the LoRA adapter UUID if an alias points to one

Resolution order:
1. Tenant's private `ModelAlias` matching `alias == request.model`
2. Public `ModelAlias` (`is_public=True`) matching the alias
3. Direct HuggingFace repo ID lookup in the `models` table
4. First available running deployment (fallback)

### vLLM Client (`services/vllm/client.py`)

A thin async httpx wrapper. Key methods:
- `health()` — `GET /health`
- `chat_completions(payload)` — `POST /v1/chat/completions`, returns parsed JSON
- `chat_completions_stream(payload)` — `POST /v1/chat/completions`, yields raw bytes chunks
- `load_lora_adapter(lora_name, lora_path)` — `POST /v1/load_lora_adapter`

All calls use `httpx.AsyncClient` with a configurable timeout (`VLLM_TIMEOUT_SECONDS`, default 120s).

### Rate Limiter (`services/limits/rate_limiter.py`)

Uses Redis with two patterns:

**Token bucket (RPM):** Lua script executed atomically on Redis — INCR a per-org key, set TTL on first write, deny if count exceeds limit. Prevents race conditions.

**Concurrency tracker:** Redis INCR/DECR on `rl:concurrent:<org_id>`. Acquired before forwarding to vLLM, released in the `finally` block of the stream generator.

**Daily quota:** Redis key `rl:daily_tokens:<org_id>:<YYYY-MM-DD>` with 25-hour TTL. Checked before acquiring concurrency slot (to avoid releasing it on quota failure).

**Global queue:** Single key `rl:queue_depth`. Provides a system-wide back-pressure valve regardless of per-tenant limits.

**Fail-open design:** All Redis calls are wrapped in try/except. If Redis is unreachable, all limit checks return `True` (allow). This is intentional — a Redis outage should degrade gracefully, not take down inference.

### Adapter Registry (`services/adapters/registry.py`)

Adapter lifecycle is asynchronous and background-processed:

```
POST /v1/adapters
  → Adapter(status='registered') inserted to DB
  → background_tasks.add_task(_process_adapter, adapter_id)
  → returns immediately

_process_adapter():
  → download_adapter()
      → adapter.status = 'downloading'
      → S3 paginator lists objects at storage_uri prefix
      → downloads all files to /tmp/inferra-adapters/<adapter_id>/
      → validate_adapter_artifact() checks adapter_config.json + rank
      → adapter.status = 'available'
  → load_adapter_into_vllm()
      → VLLMClient.load_lora_adapter(str(adapter.id), local_path)
      → adapter.status = 'loaded' / 'active'
```

Storage URIs use `s3://` or `minio://` schemes. The bucket and key prefix are parsed from the URI.

### Usage Recorder (`services/usage/recorder.py`)

Two dataclasses track per-request data:

- `RequestTimings` — monotonic timestamps at 6 points: received, routing_start, routing_end, forwarded, first_token, completed. Computes derived durations as properties.
- `TokenCounter` — parses SSE stream chunks to extract `usage.prompt_tokens` / `usage.completion_tokens` from vLLM's final chunk.

After the stream completes (or fails), `record_usage()` writes a `UsageMetric` row with all timing dimensions and a `tokens_per_second` computed field.

---

## Infrastructure Topology

### Local Development (Docker Compose)

```
Mac (localhost)
├── docker compose -f docker-compose.yml
│   ├── vllm (mock)          :8000  ← infra/mock-vllm/main.py
│   ├── api-gateway          :9100  ← VLLM_BASE_URL=http://vllm:8000
│   ├── postgres             :5432
│   ├── redis                :6379
│   ├── minio                :9000
│   ├── prometheus           :9090
│   └── grafana              :3000
```

### Real GPU Integration (RunPod + SSH tunnel)

```
Mac (localhost)
├── SSH tunnel: localhost:8001 → RunPod pod:8000
│
├── docker compose -f docker-compose.yml -f docker-compose.real.yml
│   ├── mock-vllm (health stub only)
│   ├── api-gateway :9100    ← VLLM_BASE_URL=http://host.docker.internal:8001
│   ├── postgres / redis / minio / prometheus / grafana
│
RunPod pod (5fmoz125ju1zc0)
└── vLLM :8000               ← Qwen3-4B BF16, 19 GB VRAM
```

The `docker-compose.real.yml` overlay overrides `VLLM_BASE_URL` to point through the SSH tunnel. The Worker record in PostgreSQL stores `endpoint=http://host.docker.internal:8001` — this is what `resolve_target()` uses for per-request routing, not the static env var.

---

## Security Model

| Concern | Mitigation |
|---------|-----------|
| API key exposure | Keys hashed with SHA-256; plaintext never stored |
| Admin vs inference separation | `is_admin` flag; admin keys rejected on `/v1/chat/completions` |
| Tenant data isolation | All DB queries filter on `organization_id` |
| Context length abuse | Double-enforced: gateway (estimate) + resolver (strict ceiling) |
| Resource exhaustion | Multi-layer admission control (RPM + concurrent + daily quota + global queue) |
| Adapter rank abuse | Gateway rejects adapters with `rank > max_lora_rank` |
| S3 access | MinIO credentials stored in environment variables (rotate in prod) |

---

## Configuration Reference

All configuration is via environment variables (loaded through `pydantic-settings` from `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE_URL` | `http://vllm:8000` | vLLM inference endpoint |
| `VLLM_TIMEOUT_SECONDS` | `120` | httpx timeout for vLLM calls |
| `POSTGRES_DSN` | `postgresql+asyncpg://inferra:inferra@postgres:5432/inferra` | Async PostgreSQL DSN |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `ADMIN_SECRET` | `dev-admin-secret-change-me` | Bootstrap admin key secret |
| `S3_ENDPOINT_URL` | `http://minio:9000` | MinIO / S3 endpoint |
| `S3_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `S3_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `S3_BUCKET` | `inferra-adapters` | Adapter artifact bucket |
| `ADAPTER_CACHE_DIR` | `/tmp/inferra-adapters` | Local adapter download cache |
| `MAX_CONTEXT_TOKENS` | `8192` | Hard context ceiling |
| `DEFAULT_MAX_TOKENS` | `512` | Default output token cap |
| `DEFAULT_CONTEXT_TOKENS` | `4096` | Default context window |
| `MAX_LORA_RANK` | `16` | Maximum allowed adapter rank |
| `GLOBAL_QUEUE_LIMIT` | `50` | Max concurrent in-flight requests (system-wide) |
| `OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `OTEL_ENDPOINT` | `http://localhost:4317` | OTLP collector endpoint |
| `LOG_LEVEL` | `INFO` | Python logging level |
