# Inferra — LLM Inference Platform (V1)

A production-grade, multi-tenant LLM inference platform built on vLLM. Exposes an **OpenAI-compatible API** with multi-tenant isolation, LoRA adapter management, usage metering, rate limiting, and a full observability stack.

> **Current status:** Phase 1 (GPU runtime) 90% — Stages A–E done, Stage G (8K + LoRA + prefix cache restart) is the only remaining blocker. Phases 2–8 (control plane) code-complete against mock vLLM — production-validated end-to-end by `scripts/integrate.sh` once Stage G is complete.  
> See [`STATUS.md`](STATUS.md) for the full path to production and next steps.

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

# Streaming inference
curl -N http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"test-assistant","messages":[{"role":"user","content":"Hello"}],"stream":true,"max_tokens":64}'
```

### OpenAI SDK (drop-in compatible)

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:9100/v1", api_key="<inference-key>")
response = client.chat.completions.create(
    model="test-assistant",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)
```

---

## Services

| Service | Port | Purpose |
|---------|------|---------|
| `api-gateway` | `9100` | FastAPI control plane (auth, routing, metering, adapters) |
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

---

## Integration Tests

```bash
export INFERRA_INFERENCE_KEY=...
export INFERRA_ADMIN_KEY=...
pytest tests/integration -v
```

---

## GPU Integration (RunPod L4)

Two commands on the pod, then one on your Mac:

```bash
# 1. On the pod — complete Stage G (8K ctx + LoRA + prefix caching)
bash /workspace/scripts/04_finalize_phase1.sh

# 2. On your Mac — wire all phases to real vLLM (tunnel + stack + seed + tests + benchmark)
./scripts/integrate.sh <container-id>   # container-id from RunPod dashboard → Connect → SSH
```

After `integrate.sh` completes, all 8 phases are production-validated against the real GPU. See [`docs/runbooks/how-to-run-all-phases.md`](docs/runbooks/how-to-run-all-phases.md) for the full end-to-end sequence including benchmarks and the beta checklist.

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
| [LoRA Adapters](docs/guides/lora-adapters.md) | Register and serve fine-tuned adapters |
| [Rate Limits & Quotas](docs/guides/rate-limits-and-quotas.md) | Admission control, 429/503 handling |
| [Observability](docs/guides/observability.md) | Prometheus metrics, Grafana, OTel tracing |
| [Local Development](docs/deployment/local-development.md) | Docker Compose, dev commands, local stack |
| [RunPod GPU Deployment](docs/deployment/runpod-gpu.md) | NVIDIA L4 setup, SSH tunnel, integration |
| [Contributing & Testing](docs/development/contributing.md) | Dev guide, tests, benchmark scripts |
