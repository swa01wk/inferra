# Data Model

Inferra uses **PostgreSQL 16** as its metadata store. All tables use UUID primary keys. Schema is managed by **Alembic** and applied on gateway startup via `init_db()`.

---

## Entity Relationship Overview

```
organizations
    │
    ├── api_keys (many)
    ├── quota_policies (one)
    ├── adapters (many)
    │       └── model_aliases (via adapter_id)
    └── requests (many)
            └── usage_metrics (one-to-one)

models ──────────────────── model_aliases (many)
                             │
workers ─── deployments ─── model_aliases (via deployment_id)
                 │
                 └── requests (many)
```

---

## Table Reference

### `organizations`

The top-level tenancy unit. Every API key, adapter, and request belongs to an organization.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `name` | VARCHAR | NO | — | Unique display name |
| `status` | VARCHAR | NO | `'active'` | `active` \| `suspended` |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |

**Constraints:** `UNIQUE(name)`

**Business rules:**
- A suspended organization (`status='suspended'`) cannot authenticate — `require_inference_key` returns 403.
- Created via `scripts/seed_dev_data.py` or future admin API.

---

### `api_keys`

Authentication credentials for organizations. Plaintext keys are never stored — only the SHA-256 hash.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `organization_id` | UUID | NO | — | FK → `organizations.id` |
| `name` | VARCHAR | NO | — | Human-readable label |
| `key_hash` | VARCHAR | NO | — | SHA-256 of the raw key |
| `key_prefix` | VARCHAR | NO | — | First 8 chars (for display, non-secret) |
| `status` | VARCHAR | NO | `'active'` | `active` \| `revoked` |
| `is_admin` | BOOLEAN | NO | `false` | Admin vs inference key |
| `expires_at` | TIMESTAMPTZ | YES | `NULL` | Optional expiry |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |

**Constraints:** `UNIQUE(key_hash)`

**Key format:** `inf_<32-byte-urlsafe-base64>` (total ~47 chars). The prefix `inf_` aids secret scanning. The first 8 characters become `key_prefix` for safe display.

**Auth flow:**
1. Extract Bearer token from `Authorization` header
2. Compute `SHA256(token)`
3. `SELECT * FROM api_keys WHERE key_hash = ? AND status = 'active'`
4. If found, check `expires_at` in Python

---

### `quota_policies`

Per-organization rate limit and quota configuration. One row per organization (one-to-one relationship, enforced by `UNIQUE(organization_id)`).

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `organization_id` | UUID | NO | — | FK → `organizations.id`, UNIQUE |
| `rpm_limit` | INTEGER | NO | `60` | Requests per minute |
| `max_concurrent_requests` | INTEGER | NO | `5` | Max in-flight requests |
| `max_input_tokens` | INTEGER | NO | `8192` | Max prompt token estimate |
| `max_output_tokens` | INTEGER | NO | `2048` | Max output tokens (caps request's max_tokens) |
| `daily_token_soft_limit` | BIGINT | YES | `NULL` | Soft limit (warning only, not enforced yet) |
| `daily_token_hard_limit` | BIGINT | YES | `NULL` | Hard limit (enforced; `NULL` = unlimited) |
| `monthly_token_hard_limit` | BIGINT | YES | `NULL` | Monthly hard limit (future use) |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last modification |

**If no row exists** for an organization, `get_or_default_policy()` returns an in-memory default:
- `rpm_limit=60`, `max_concurrent_requests=5`, `max_input_tokens=8192`, `max_output_tokens=2048`, `daily_token_hard_limit=1_000_000`

---

### `models`

The catalog of base models available on the platform. Entries are seeded by `seed_dev_data.py` or the admin.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `hf_repo` | VARCHAR | NO | — | HuggingFace repository ID (e.g., `Qwen/Qwen3-4B`) |
| `display_name` | VARCHAR | YES | `NULL` | Human-readable name |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |

**Constraints:** `UNIQUE(hf_repo)`

---

### `workers`

GPU compute nodes that host the vLLM process. In V1 there is one worker (the RunPod L4 pod).

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `hostname` | VARCHAR | NO | — | Identifier (e.g., `runpod-l4-v1`) |
| `gpu_type` | VARCHAR | NO | — | GPU model string (e.g., `NVIDIA L4`) |
| `gpu_vram_mb` | INTEGER | NO | — | Total VRAM in MiB |
| `endpoint` | VARCHAR | NO | — | vLLM HTTP endpoint (e.g., `http://host.docker.internal:8001`) |
| `status` | VARCHAR | NO | `'healthy'` | `healthy` \| `unhealthy` |
| `last_heartbeat` | TIMESTAMPTZ | YES | `NULL` | Last successful health check (future) |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |

**V1 note:** Worker health is not automatically updated. Status is manually managed or via `seed_real_worker.py`.

---

### `deployments`

A deployment links a model to a worker and represents a running vLLM serving instance.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `model_id` | UUID | NO | — | FK → `models.id` |
| `worker_id` | UUID | NO | — | FK → `workers.id` |
| `endpoint` | VARCHAR | NO | — | Direct endpoint URL (duplicates worker.endpoint for query efficiency) |
| `config_json` | JSONB | NO | `{}` | vLLM launch flags, max_model_len, etc. |
| `status` | VARCHAR | NO | `'running'` | `running` \| `stopped` \| `failed` |
| `started_at` | TIMESTAMPTZ | YES | `NULL` | When vLLM became healthy |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |

**Routing:** `resolve_target()` queries `SELECT ... WHERE status='running' LIMIT 1`. V1 supports one active deployment.

---

### `adapters`

LoRA adapter artifacts registered by a tenant. Each adapter is tied to a base model and an organization.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `organization_id` | UUID | NO | — | FK → `organizations.id` |
| `base_model_id` | UUID | NO | — | FK → `models.id` |
| `name` | VARCHAR | NO | — | Tenant-scoped unique name |
| `display_name` | VARCHAR | YES | `NULL` | Optional display label |
| `storage_uri` | VARCHAR | NO | — | `s3://bucket/prefix` or `minio://bucket/prefix` |
| `local_path` | VARCHAR | YES | `NULL` | Local cache path after download |
| `rank` | INTEGER | NO | — | LoRA rank (must be ≤ `MAX_LORA_RANK=16`) |
| `status` | VARCHAR | NO | `'registered'` | State machine (see below) |
| `error_message` | TEXT | YES | `NULL` | Failure reason if status=`failed` |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | NO | `now()` | Last state change |

**Constraints:** `UNIQUE(organization_id, name)`

**State machine:**
```
registered
    │
    ▼
downloading    ←── background task starts
    │
    ├──[error]──► failed
    │
    ▼
available      ←── S3 download complete, rank validated
    │
    ├──[no running deployment]──► failed
    │
    ▼
loaded         ←── vLLM load_lora_adapter() succeeded
    │
    ▼
active         ←── confirmed ready to serve traffic
    │
    ▼
deleted        ←── soft delete via DELETE /v1/adapters/{id}
```

---

### `model_aliases`

Human-readable names that resolve to a (base model, optional adapter, deployment) tuple. Clients use the alias as the `model` field in chat completions.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | `uuid4()` | Primary key |
| `organization_id` | UUID | NO | — | FK → `organizations.id` |
| `alias` | VARCHAR | NO | — | The logical model name (e.g., `my-assistant`) |
| `base_model_id` | UUID | NO | — | FK → `models.id` |
| `adapter_id` | UUID | YES | `NULL` | FK → `adapters.id` (NULL = base model only) |
| `deployment_id` | UUID | YES | `NULL` | FK → `deployments.id` |
| `is_public` | BOOLEAN | NO | `false` | If true, accessible by any tenant |
| `created_at` | TIMESTAMPTZ | NO | `now()` | Creation timestamp |

**Constraints:** `UNIQUE(organization_id, alias)`

**Alias resolution order in `resolve_target()`:**
1. `WHERE organization_id = <caller_org_id> AND alias = <model>`
2. `WHERE alias = <model> AND is_public = true`
3. Direct HuggingFace repo ID lookup
4. First running deployment fallback

---

### `requests`

Audit log of every inference request. One row per call to `POST /v1/chat/completions`.

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `id` | UUID | NO | (from resolver) | Primary key; also the `X-Request-ID` |
| `organization_id` | UUID | NO | — | FK → `organizations.id` |
| `api_key_id` | UUID | NO | — | FK → `api_keys.id` |
| `deployment_id` | UUID | YES | `NULL` | FK → `deployments.id` |
| `adapter_id` | UUID | YES | `NULL` | FK → `adapters.id` (if adapter was used) |
| `logical_model` | VARCHAR | NO | — | The `model` field from the request |
| `base_model_id` | UUID | YES | `NULL` | FK → `models.id` |
| `status` | VARCHAR | NO | `'pending'` | `pending` \| `completed` \| `failed` \| `cancelled` |
| `http_status` | INTEGER | YES | `NULL` | HTTP response code (200, 500, etc.) |
| `error_code` | VARCHAR | YES | `NULL` | Short error description if failed |
| `cancelled` | BOOLEAN | NO | `false` | True if client disconnected mid-stream |
| `received_at` | TIMESTAMPTZ | NO | `now()` | Gateway receipt timestamp |
| `first_token_at` | TIMESTAMPTZ | YES | `NULL` | When first content token was sent |
| `completed_at` | TIMESTAMPTZ | YES | `NULL` | When stream ended |

---

### `usage_metrics`

Detailed latency and token accounting for each request. One row per request (1:1 via FK on `request_id`).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | UUID | NO | Primary key |
| `request_id` | UUID | NO | FK → `requests.id`, UNIQUE |
| `prompt_tokens` | INTEGER | NO | Prompt tokens (from vLLM usage field) |
| `completion_tokens` | INTEGER | NO | Generated tokens |
| `total_tokens` | INTEGER | NO | `prompt + completion` |
| `gateway_ms` | INTEGER | YES | Time from receipt to routing start |
| `routing_ms` | INTEGER | YES | Time to resolve target |
| `queue_ms` | INTEGER | YES | Time in queue (future; not yet set) |
| `ttft_ms` | INTEGER | YES | Time-to-first-token from forwarding |
| `decode_ms` | INTEGER | YES | Time from first to last token |
| `total_ms` | INTEGER | YES | Full end-to-end wall clock |
| `tokens_per_second` | FLOAT | YES | `completion_tokens / (decode_ms / 1000)` |
| `time_per_output_token_ms` | FLOAT | YES | `decode_ms / completion_tokens` |
| `worker_id` | UUID | YES | FK → `workers.id` |
| `vllm_version` | VARCHAR | YES | vLLM version (future) |
| `created_at` | TIMESTAMPTZ | NO | Record creation timestamp |

---

## Redis Data Layout

Redis is used exclusively for rate limiting and queue management. Keys expire automatically.

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `rl:rpm:<org_id>` | STRING (int) | 60s | RPM token bucket counter |
| `rl:concurrent:<org_id>` | STRING (int) | 300s | Active concurrent request count |
| `rl:daily_tokens:<org_id>:<YYYY-MM-DD>` | STRING (int) | 90000s (~25h) | Daily token accumulator |
| `rl:queue_depth` | STRING (int) | (no expiry) | Global in-flight request count |

---

## MinIO / S3 Data Layout

| Path | Description |
|------|-------------|
| `inferra-adapters/<bucket>/` | Root bucket for all adapter artifacts |
| `<storage_uri prefix>/adapter_config.json` | LoRA config (rank, base model, etc.) |
| `<storage_uri prefix>/*.safetensors` | Model weight files |

Local download cache: `/tmp/inferra-adapters/<adapter_id>/`

---

## Migration Strategy

Alembic is configured with `asyncpg`. `init_db()` (called at gateway startup) runs `alembic upgrade head` automatically, so schema migrations apply on deploy with no manual step. New migrations should be generated with:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```
