# Inferra — LLM Inference Platform (V1)

A production-grade, multi-tenant LLM inference platform built on vLLM. Exposes an **OpenAI-compatible API** with multi-tenant isolation, LoRA adapter management, usage metering, rate limiting, and a full observability stack. Includes a **React chat UI** for browser-based inference testing.

> **Current status (2026-08-30):** All 8 phases + frontend production-validated. E2E tested: browser → FastAPI gateway → Qwen/Qwen3-4B on RunPod L4. **31/32 integration tests passed**, ~28 tok/s, TTFT 610 ms.  
> See [`STATUS.md`](STATUS.md) for full details and next steps.

---

## Quick Start (Local)

```bash
docker compose up --build -d
docker compose exec api-gateway python scripts/seed_dev_data.py
```

Copy the printed `INFERENCE_KEY` and `ADMIN_KEY`, then:

```bash
export INFERRA_INFERENCE_KEY=<inference-key>
export INFERRA_ADMIN_KEY=<admin-key>

# Health check
curl http://localhost:9100/health

# Non-streaming inference
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"test-assistant","messages":[{"role":"user","content":"Hello"}],"max_tokens":64}'
```

### Start the frontend (Chat UI)

```bash
cd inferra-ui
npm install          # first time only
npm run dev
```

Open the printed URL (e.g. `http://localhost:5173`), click **Settings**, and enter:
- **Gateway URL** → the same URL you see in the browser bar (routes via Vite proxy)
- **Inference Key** → from `seed_dev_data.py` output
- **Admin Key** → from `seed_dev_data.py` output

See [`docs/guides/frontend-guide.md`](docs/guides/frontend-guide.md) for the full walkthrough.

---

## Services

| Service | Port | Purpose |
|---------|------|---------|
| `api-gateway` | `9100` | FastAPI control plane (auth, routing, metering, adapters) |
| `inferra-ui` | `5173–5175` | React + Vite chat UI and admin dashboard |
| `vllm` | internal | Inference engine — mock stub locally, real vLLM on RunPod GPU |
| `postgres` | internal | Metadata store (orgs, keys, adapters, requests, usage) |
| `redis` | internal | Rate limits, concurrency tracking, daily quotas |
| `minio` | internal / `9001` | LoRA adapter artifact storage (S3-compatible) |
| `prometheus` | internal | Metrics collection |
| `grafana` | `3000` | Pre-built dashboards (admin/admin) |

---

## Key Features

- **OpenAI-compatible** — `POST /v1/chat/completions` with streaming SSE and non-streaming modes
- **Multi-tenant** — per-org API keys, isolated usage records, private LoRA adapters
- **LoRA adapter management** — register → download from S3 → load into vLLM → serve by alias
- **Rate limiting** — RPM, concurrency, daily token quota, global queue depth (Redis)
- **Usage metering** — full latency decomposition (TTFT, decode, total) + token counts per request
- **Observability** — Prometheus metrics, Grafana dashboards, optional OpenTelemetry tracing
- **Chat UI** — React SPA with ChatGPT-style playground, Qwen3 thinking mode, all admin pages

---

## Frontend (inferra-ui)

| Page | URL | Auth | What it does |
|------|-----|------|--------------|
| Chat | `/chat` | Inference key | Streaming/non-streaming chat; model picker; thinking toggle; TTFT stats |
| API Keys | `/keys` | Admin key | Create inference keys; show secret once; revoke |
| Adapters | `/adapters` | Inference key | Register LoRA adapters; live status polling |
| Usage | `/usage` | Inference key | Per-request latency breakdown; TTFT/decode chart |
| Workers | `/workers` | Admin key | GPU worker info; deployment config; Grafana embed |

**Configuration:** Gateway URL, Inference Key, and Admin Key are stored in `localStorage` via the Settings modal (gear icon in top-right).

---

## Integration Tests

```bash
export INFERRA_INFERENCE_KEY=...
export INFERRA_ADMIN_KEY=...
pytest tests/integration -v
```

---

## GPU Integration (RunPod L4)

The stack has been validated end-to-end against real GPU inference. To re-run against a new pod session:

```bash
# On your Mac — wire all phases to real vLLM (stack + seed + tests + benchmark)
./scripts/integrate.sh <container-id>   # container-id from RunPod dashboard → Connect → SSH
```

**Prerequisites:** vLLM running on the pod with 8K ctx + LoRA enabled, cloudflared tunnel active (`tmux attach -t inferra:cf-tunnel`).

To run the full Phase 8 benchmark suite (concurrency sweep, context sweep, LoRA mix, prefix cache, overload stress):

```bash
BENCHMARK_URL=http://localhost:9100/v1/chat/completions \
API_KEY=$INFERRA_INFERENCE_KEY \
MODEL=test-assistant \
bash scripts/runpod/06_run_all_benchmarks.sh
```

See [`STATUS.md`](STATUS.md) for baseline numbers and the beta checklist.

---

## Documentation

Full documentation is in [`docs/`](docs/README.md):

| Document | Description |
|----------|-------------|
| [Product Overview](docs/product/overview.md) | What Inferra is, capabilities, V1 scope |
| [System Architecture](docs/architecture/system-architecture.md) | Components, request lifecycle, config reference |
| [Data Model](docs/architecture/data-model.md) | PostgreSQL schema, all tables, Redis layout |
| [API Reference](docs/api/api-reference.md) | Every endpoint with examples |
| [Authentication](docs/api/authentication.md) | API key types, lifecycle, security model |
| [Getting Started](docs/guides/getting-started.md) | First inference call in 5 minutes |
| [**Frontend Guide**](docs/guides/frontend-guide.md) | **Run the React UI, configure keys, use all 5 pages** |
| [LoRA Adapters](docs/guides/lora-adapters.md) | Register and serve fine-tuned adapters |
| [Rate Limits & Quotas](docs/guides/rate-limits-and-quotas.md) | Admission control, 429/503 handling |
| [Observability](docs/guides/observability.md) | Prometheus metrics, Grafana, OTel tracing |
| [Local Development](docs/deployment/local-development.md) | Docker Compose, dev commands, local stack |
| [**E2E Integration**](docs/deployment/e2e-integration.md) | **RunPod + gateway + frontend, session resume** |
| [RunPod GPU Deployment](docs/deployment/runpod-gpu.md) | NVIDIA L4 setup, SSH tunnel, integration |
| [Contributing & Testing](docs/development/contributing.md) | Dev guide, tests, benchmark scripts |
