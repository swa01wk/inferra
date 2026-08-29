# Local Development

This document covers running the full Inferra stack locally using Docker Compose with a mock vLLM engine. This is the standard development environment for working on the control plane (gateway, auth, rate limiting, metering, adapters, observability) without needing GPU hardware.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | ≥ 4.20 | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Docker Compose | v2 (bundled with Docker Desktop) | — |
| Python | 3.9+ | For running scripts outside Docker |
| git | any | — |

---

## Repository Layout

```
inferra/
├── apps/
│   └── api/                    ← FastAPI API gateway
│       ├── main.py             ← Application entry point
│       ├── config.py           ← Settings (pydantic-settings)
│       ├── routes/             ← Endpoint handlers
│       │   ├── chat.py         ← POST /v1/chat/completions
│       │   ├── adapters.py     ← LoRA adapter CRUD
│       │   ├── admin.py        ← Key/worker/deployment management
│       │   ├── health.py       ← GET /health
│       │   ├── metrics.py      ← GET /metrics (Prometheus)
│       │   └── models.py       ← GET /v1/models
│       ├── schemas/            ← Pydantic request/response models
│       ├── services/
│       │   ├── auth/           ← API key hashing + validation
│       │   ├── adapters/       ← S3 download + vLLM load
│       │   ├── limits/         ← Redis rate limiting + admission control
│       │   ├── observability/  ← Prometheus metrics + OTel tracing
│       │   ├── routing/        ← Model alias → worker resolution
│       │   ├── usage/          ← Request timing + token recording
│       │   └── vllm/           ← httpx client for vLLM API
│       └── middleware/
│           └── logging.py      ← X-Request-ID injection
│
├── db/
│   ├── models/                 ← SQLAlchemy ORM models
│   └── session.py              ← AsyncEngine + session factory
│
├── infra/
│   ├── docker/
│   │   ├── api.Dockerfile      ← Gateway image
│   │   ├── nginx.conf          ← (future) reverse proxy config
│   │   └── versions.env        ← Pinned image versions
│   ├── mock-vllm/
│   │   ├── main.py             ← Mock OpenAI-compatible server
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── grafana/                ← Dashboard + provisioning config
│   └── prometheus/
│       └── prometheus.yml
│
├── scripts/
│   ├── seed_dev_data.py        ← Seeds org, keys, model, worker, deployment, alias
│   ├── seed_real_worker.py     ← Seeds real RunPod worker + retires mock
│   ├── integrate.sh            ← Mac-side: tunnel + stack + seed + test (one shot)
│   ├── benchmark/              ← Baseline, concurrency, context, LoRA, overload scripts
│   └── runpod/                 ← Pod-side scripts (GPU validation, vLLM serve, etc.)
│
├── tests/
│   ├── integration/            ← pytest integration tests (requires running stack)
│   └── load/
│       └── locustfile.py       ← Locust load test
│
├── plans/                      ← Phase-by-phase implementation plans
├── docs/                       ← This documentation
├── docker-compose.yml          ← Base compose (mock vLLM)
├── docker-compose.real.yml     ← Overlay for real vLLM / RunPod
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## First-Time Setup

### 1. Clone and configure

```bash
git clone <repo-url> inferra
cd inferra

cp .env.example .env
# Edit .env if you need custom values (defaults work for local dev)
```

`.env.example` contents:
```env
VLLM_BASE_URL=http://vllm:8000
POSTGRES_DSN=postgresql+asyncpg://inferra:inferra@postgres:5432/inferra
REDIS_URL=redis://redis:6379/0
ADMIN_SECRET=dev-admin-secret-change-me
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=inferra-adapters
MAX_CONTEXT_TOKENS=8192
GLOBAL_QUEUE_LIMIT=50
LOG_LEVEL=INFO
```

### 2. Build and start

```bash
docker compose up --build -d
```

First build takes ~2 minutes (downloading base images, installing Python deps). Subsequent starts are ~10 seconds.

### 3. Wait for services to be ready

```bash
docker compose ps
```

Wait until all services show `healthy` or `running`:
```
NAME                    STATUS          PORTS
inferra-api-gateway-1   Up (healthy)    0.0.0.0:9100->9000/tcp
inferra-postgres-1      Up (healthy)
inferra-redis-1         Up (healthy)
inferra-vllm-1          Up (healthy)
inferra-minio-1         Up
inferra-prometheus-1    Up
inferra-grafana-1       Up              0.0.0.0:3000->3000/tcp
```

### 4. Seed development data

```bash
docker compose exec api-gateway python scripts/seed_dev_data.py
```

This creates:
- Organization: `Dev Org`
- Admin API key (`is_admin=True`)
- Inference API key (`is_admin=False`)
- Model entry: `Qwen/Qwen3-4B`
- Worker entry: `mock-vllm` (endpoint: `http://vllm:8000`, status: `healthy`)
- Deployment: `running` (links model to worker)
- Model alias: `test-assistant` → `Qwen/Qwen3-4B` (public)

Export the printed keys:
```bash
export INFERRA_ADMIN_KEY=inf_...
export INFERRA_INFERENCE_KEY=inf_...
```

---

## Mock vLLM

The mock vLLM server (`infra/mock-vllm/main.py`) is a lightweight Flask application that implements the OpenAI API contract with synthetic responses:

- `GET /health` → `{"status": "healthy"}`
- `GET /v1/models` → list with `Qwen/Qwen3-4B`
- `POST /v1/chat/completions` → configurable synthetic tokens with realistic timing
- `POST /v1/load_lora_adapter` → immediate success (no actual loading)

Controlled by environment variables:
```env
MOCK_COMPLETION_TOKENS=32        # tokens per response
MOCK_TOKEN_DELAY_MS=20           # delay between streaming tokens
MOCK_TTFT_DELAY_MS=50            # time before first token
```

The mock is intentionally simple — it validates that the gateway correctly handles the OpenAI wire format but does not simulate GPU memory, real tokenization, or model behavior.

---

## Common Development Commands

### View gateway logs

```bash
docker compose logs -f api-gateway
```

### Restart only the gateway (after code changes)

```bash
docker compose up --build -d api-gateway
```

The gateway image is rebuilt from `infra/docker/api.Dockerfile`. Postgres, Redis, and MinIO data persist across restarts.

### Open a Python shell in the gateway container

```bash
docker compose exec api-gateway python
```

### Run a database query

```bash
docker compose exec postgres psql -U inferra -d inferra -c "SELECT * FROM organizations;"
```

### Inspect Redis

```bash
docker compose exec redis redis-cli keys "rl:*"
docker compose exec redis redis-cli get "rl:queue_depth"
```

### Reset everything (wipe all data)

```bash
docker compose down -v    # removes volumes (postgres + minio data)
docker compose up --build -d
docker compose exec api-gateway python scripts/seed_dev_data.py
```

---

## Environment Variables

All gateway configuration is in `apps/api/config.py` and loaded from `.env`:

| Variable | Default | Notes |
|----------|---------|-------|
| `VLLM_BASE_URL` | `http://vllm:8000` | Points to mock in local dev |
| `POSTGRES_DSN` | `postgresql+asyncpg://inferra:inferra@postgres:5432/inferra` | — |
| `REDIS_URL` | `redis://redis:6379/0` | — |
| `ADMIN_SECRET` | `dev-admin-secret-change-me` | Bootstrap secret (not an API key itself) |
| `S3_ENDPOINT_URL` | `http://minio:9000` | MinIO local endpoint |
| `S3_ACCESS_KEY` | `minioadmin` | — |
| `S3_SECRET_KEY` | `minioadmin` | — |
| `S3_BUCKET` | `inferra-adapters` | Created automatically at startup |
| `MAX_CONTEXT_TOKENS` | `8192` | Hard context ceiling |
| `DEFAULT_MAX_TOKENS` | `512` | Default output cap |
| `MAX_LORA_RANK` | `16` | Max adapter rank |
| `GLOBAL_QUEUE_LIMIT` | `50` | System-wide request gate |
| `OTEL_ENABLED` | `false` | Set to `true` + `OTEL_ENDPOINT` to enable tracing |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose output |

---

## Docker Images

| Service | Image | Notes |
|---------|-------|-------|
| `api-gateway` | Built from `infra/docker/api.Dockerfile` | Python 3.11-slim, installs `requirements.txt` |
| `vllm` (mock) | Built from `infra/mock-vllm/Dockerfile` | Flask, minimal deps |
| `postgres` | `postgres:16-alpine` | Pinned to major version |
| `redis` | `redis:7-alpine` | `maxmemory 256mb`, `allkeys-lru` |
| `minio` | `minio/minio:latest` | S3-compatible object storage |
| `prometheus` | `prom/prometheus:v2.52.0` | Pinned |
| `grafana` | `grafana/grafana:10.4.2` | Pinned |

---

## Port Reference

| Port | Service | Accessible From |
|------|---------|-----------------|
| `9100` | API gateway (external) | Host machine |
| `3000` | Grafana | Host machine |
| `9001` | MinIO console | Host machine (for browsing adapters) |
| `9000` | MinIO API | Internal (Docker network) |
| `5432` | PostgreSQL | Internal |
| `6379` | Redis | Internal |
| `9090` | Prometheus | Internal (or expose in compose for direct access) |

---

## Switching to Real vLLM

When a RunPod GPU is available, override the compose config:

```bash
docker compose -f docker-compose.yml -f docker-compose.real.yml up -d
```

`docker-compose.real.yml` overrides `VLLM_BASE_URL` to point through an SSH tunnel:
```yaml
services:
  api-gateway:
    environment:
      VLLM_BASE_URL: http://host.docker.internal:8001
```

See [RunPod GPU Deployment](runpod-gpu.md) for the full integration workflow including SSH tunnel setup.

---

## Python Virtual Environment (for running scripts outside Docker)

For running scripts directly on the Mac (not inside Docker):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then scripts like `seed_dev_data.py` can be run with the local venv, pointing at `localhost:9100` instead of the internal Docker hostname.
