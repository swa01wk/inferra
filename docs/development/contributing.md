# Contributing & Testing

This document covers the development workflow, testing strategy, benchmark scripts, and codebase conventions for contributing to Inferra.

---

## Development Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url> inferra
cd inferra

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start the local stack

```bash
docker compose up --build -d
docker compose exec api-gateway python scripts/seed_dev_data.py
```

### 3. Export API keys

```bash
export INFERRA_INFERENCE_KEY=inf_...
export INFERRA_ADMIN_KEY=inf_...
export INFERRA_BASE_URL=http://localhost:9100
```

---

## Project Structure

```
apps/api/
├── main.py                     ← FastAPI app + lifespan (startup/shutdown)
├── config.py                   ← All env-var settings (pydantic-settings)
├── middleware/logging.py       ← X-Request-ID injection + access log
├── routes/
│   ├── chat.py                 ← POST /v1/chat/completions (core inference)
│   ├── adapters.py             ← LoRA adapter CRUD + alias management
│   ├── admin.py                ← API key management + usage query
│   ├── admin_ui.py             ← HTML admin UI (Jinja2)
│   ├── health.py               ← GET /health
│   ├── metrics.py              ← GET /metrics (Prometheus)
│   └── models.py               ← GET /v1/models (proxied from vLLM)
├── schemas/__init__.py         ← All Pydantic request/response models
└── services/
    ├── auth/keys.py            ← SHA-256 hashing + key validation
    ├── adapters/registry.py    ← S3 download + vLLM LoRA loading
    ├── limits/
    │   ├── admission.py        ← Orchestrates all admission checks
    │   └── rate_limiter.py     ← Redis Lua token bucket + concurrent tracker
    ├── observability/
    │   ├── metrics.py          ← Prometheus counters/histograms/gauges
    │   └── tracing.py          ← OpenTelemetry setup
    ├── routing/resolver.py     ← Model alias → deployment → worker resolution
    ├── usage/recorder.py       ← Request timing + token counting + DB write
    └── vllm/client.py          ← httpx async client for vLLM API

db/
├── models/                     ← SQLAlchemy ORM (one file per entity)
│   ├── organization.py
│   ├── api_key.py
│   ├── model.py
│   ├── worker.py
│   ├── deployment.py
│   ├── adapter.py              ← Adapter + ModelAlias
│   ├── quota_policy.py
│   └── request.py              ← RequestRecord + UsageMetric
└── session.py                  ← AsyncEngine + session factory + init_db()
```

---

## Code Conventions

### Python style
- Python 3.9+ with type hints throughout
- `from __future__ import annotations` in all files using `X | Y` union syntax
- `pydantic` v2 for all request/response schemas
- `sqlalchemy` v2 async-native ORM (`AsyncSession`, `select()`, not legacy `Query`)
- Structured JSON logging for all audit events (key created, adapter loaded, etc.)

### Error handling
- All route handlers raise `HTTPException` — never return raw error dicts
- Service functions raise `ValueError` or `Exception` — routes translate to `HTTPException`
- Redis failures always fail-open (except daily quota which fails closed)
- Background tasks (`BackgroundTasks`) never block the response; use `AsyncSessionLocal` independently

### Database access
- Routes receive `AsyncSession` via `Depends(get_db)` — transaction is committed per request
- Background tasks (e.g., `_finalize_usage`) open their own `AsyncSessionLocal()` context
- All queries filter on `organization_id` to enforce tenant isolation

### Async patterns
- All I/O is async: database (asyncpg), Redis (aioredis), HTTP (httpx)
- `VLLMClient` uses `httpx.AsyncClient` as a context manager — no connection reuse across requests
- Streaming responses use `StreamingResponse` with an async generator that yields raw SSE bytes

---

## Integration Tests

Integration tests run against the full Docker Compose stack. They require the stack to be running and API keys to be exported.

### Run all integration tests

```bash
export INFERRA_INFERENCE_KEY=inf_...
export INFERRA_ADMIN_KEY=inf_...

pytest tests/integration -v
```

### Run a specific test file

```bash
pytest tests/integration/test_inference.py -v
pytest tests/integration/test_auth.py -v
pytest tests/integration/test_rate_limits.py -v
pytest tests/integration/test_adapters.py -v
pytest tests/integration/test_usage.py -v
pytest tests/integration/test_health.py -v
```

### Test files and coverage

| File | Tests |
|------|-------|
| `test_health.py` | `GET /health` returns 200 + vLLM status |
| `test_auth.py` | Invalid key → 401; admin key on inference → 403; revoked key → 401 |
| `test_inference.py` | Non-streaming + streaming chat; context limit → 400; max_tokens cap |
| `test_adapters.py` | Adapter registration; status polling; alias creation; duplicate name → 409 |
| `test_usage.py` | `GET /v1/usage` returns request records with token counts |
| `test_rate_limits.py` | Rapid fire → 429; verify `Retry-After` header present |

### Test environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INFERRA_BASE_URL` | `http://localhost:9100` | Gateway base URL |
| `INFERRA_INFERENCE_KEY` | `""` | Inference API key (tests skip if not set) |
| `INFERRA_ADMIN_KEY` | `""` | Admin API key |
| `INFERRA_MODEL` | `"test-assistant"` | Model alias used in inference tests |

Tests decorated with `@pytest.mark.skipif(not INFERENCE_KEY, ...)` are skipped automatically if the key is not exported — this prevents accidental failures in CI environments without credentials.

---

## Benchmark Scripts

All benchmark scripts are in `scripts/benchmark/`. They require the stack to be running with either mock or real vLLM. For Phase 8 runs, use `scripts/runpod/06_run_all_benchmarks.sh` to run all stages in sequence.

### `baseline.py` — Multi-profile latency baseline

Runs **four prompt profiles** (short/medium/long/2K-context) plus a streaming TTFT measurement. Outputs structured JSON for `report.py`.

```bash
python scripts/benchmark/baseline.py \
    --url http://localhost:9100/v1/chat/completions \
    --api-key $INFERRA_INFERENCE_KEY \
    --model test-assistant \
    --output /workspace/benchmarks/baseline.json
```

Output (JSON):
```json
{
  "benchmark": "baseline",
  "results": [
    {"label": "short_chat", "total_ms": 843, "prompt_tokens": 64, "completion_tokens": 128, "tokens_per_second": 51.8},
    {"label": "medium_chat", "total_ms": 2100, "prompt_tokens": 512, "completion_tokens": 256, ...},
    {"label": "streaming_ttft", "ttft_ms": 312, "tokens_per_second": 48.3, "streaming_ok": true}
  ]
}
```

### `concurrency.py` — Concurrency sweep

Runs N simultaneous requests and measures TTFT/latency at each concurrency level. Stops if error rate exceeds 2%.

```bash
python scripts/benchmark/concurrency.py \
    --url http://localhost:9100/v1/chat/completions \
    --api-key $INFERRA_INFERENCE_KEY \
    --concurrency 1 2 4 8 16 \
    --requests-per-level 20 \
    --output /workspace/benchmarks/concurrency.json
```

### `context_sweep.py` — Context length impact

Tests TTFT and total latency at 2K/4K/8K context sizes at concurrency 1, 4, and 8.

```bash
python scripts/benchmark/context_sweep.py \
    --url http://localhost:9100/v1/chat/completions \
    --api-key $INFERRA_INFERENCE_KEY \
    --context-sizes 2048 4096 8192 \
    --output /workspace/benchmarks/context_sweep.json
```

### `lora_mix.py` — Base model vs LoRA overhead

Measures TTFT when mixing base model and LoRA adapter requests (requires a registered active adapter).

```bash
python scripts/benchmark/lora_mix.py \
    --url http://localhost:9100/v1/chat/completions \
    --api-key $INFERRA_INFERENCE_KEY \
    --lora-alias lora-assistant \
    --output /workspace/benchmarks/lora_mix.json
```

### `prefix_cache.py` — Prefix cache effectiveness

Sends 8 requests with the same 2K system prompt and measures TTFT improvement (cold vs warm cache).

```bash
python scripts/benchmark/prefix_cache.py \
    --url http://localhost:9100/v1/chat/completions \
    --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/prefix_cache.json
```

### `overload.py` — Admission control stress test

Sends 2× RPM burst then 3× concurrent burst; verifies 429/503 pattern and that accepted requests are not degraded.

```bash
python scripts/benchmark/overload.py \
    --url http://localhost:9100/v1/chat/completions \
    --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/overload.json
```

### `report.py` — Aggregate capacity report

Reads all JSON outputs and writes `docs/architecture/v1-capacity-report.md`.

```bash
python scripts/benchmark/report.py \
    --baseline   /workspace/benchmarks/baseline.json \
    --concurrency /workspace/benchmarks/concurrency.json \
    --context    /workspace/benchmarks/context_sweep.json \
    --lora       /workspace/benchmarks/lora_mix.json \
    --prefix     /workspace/benchmarks/prefix_cache.json \
    --overload   /workspace/benchmarks/overload.json \
    --report-path docs/architecture/v1-capacity-report.md
```

### One-command: `06_run_all_benchmarks.sh` (Phase 8 orchestrator)

Runs all 7 benchmark stages in sequence and auto-generates the capacity report:

```bash
# Against gateway (requires stack up + inference key)
BENCHMARK_URL=http://localhost:9100/v1/chat/completions \
API_KEY=$INFERRA_INFERENCE_KEY \
bash scripts/runpod/06_run_all_benchmarks.sh

# Against vLLM directly on pod
bash /workspace/scripts/06_run_all_benchmarks.sh
```

---

## Load Testing (Locust)

For sustained load testing with configurable concurrency. The locustfile sends a mix of short, medium, streaming, and long-prompt tasks.

```bash
# Interactive (web UI at http://localhost:8089)
locust -f tests/load/locustfile.py \
    --host http://localhost:9100 \
    --api-key $INFERRA_INFERENCE_KEY

# Headless (CI / automated)
locust -f tests/load/locustfile.py \
    --host http://localhost:9100 \
    --api-key $INFERRA_INFERENCE_KEY \
    --users 10 \
    --spawn-rate 2 \
    --run-time 60s \
    --headless \
    --html reports/load-test.html
```

Task mix:
| Task | Weight | What it sends |
|------|--------|--------------|
| `short_chat` | 3× | `max_tokens=32`, non-streaming |
| `medium_chat` | 2× | `max_tokens=256`, non-streaming |
| `streaming_chat` | 2× | `max_tokens=128`, SSE streaming (validates `[DONE]`) |
| `long_prompt_chat` | 1× | ~1K token prompt, `max_tokens=256` |
| `health_check` | 1× | `GET /health` |

---

## Adding a New Route

1. Create the handler in `apps/api/routes/<name>.py`
2. Register in `apps/api/main.py`:
   ```python
   from apps.api.routes import my_new_route
   app.include_router(my_new_route.router, prefix="/v1")
   ```
3. Add Pydantic schemas to `apps/api/schemas/__init__.py`
4. Write integration tests in `tests/integration/test_<name>.py`

### Route handler template

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from apps.api.services.auth.keys import AuthenticatedContext, require_inference_key
from db.session import get_db

router = APIRouter()

@router.get("/my-resource")
async def get_my_resource(
    auth: AuthenticatedContext = Depends(require_inference_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # All queries must filter on auth.organization.id for tenant isolation
    ...
```

---

## Adding a Database Model

1. Create `db/models/<name>.py` with the SQLAlchemy model
2. Import it in `db/models/__init__.py`
3. Generate a migration:
   ```bash
   alembic revision --autogenerate -m "add <table_name>"
   ```
4. Review the generated migration in `alembic/versions/`
5. Apply:
   ```bash
   alembic upgrade head
   ```
   (or restart the gateway — `init_db()` calls `alembic upgrade head` automatically)

---

## Dependency Management

Dependencies are in `requirements.txt`, pinned to exact versions for the beta tag.

To add a new dependency:

```bash
pip install <package>
# Find the exact installed version
pip show <package> | grep Version
# Add to requirements.txt with the exact version:
echo "<package>==X.Y.Z" >> requirements.txt
```

Then rebuild the gateway container:
```bash
docker compose up --build -d api-gateway
```

> **Note:** `requirements.txt` uses exact `==` pins (not `>=` ranges) for the beta release.
> The load testing dependency (`locust`) is excluded from the production install — install it separately for benchmarking:
> ```bash
> pip install locust==2.32.2
> ```

---

## Key Files for Common Tasks

| Task | File(s) |
|------|---------|
| Change rate limit defaults | `apps/api/services/limits/admission.py` → `get_or_default_policy()` |
| Add a new Prometheus metric | `apps/api/services/observability/metrics.py` |
| Change routing logic | `apps/api/services/routing/resolver.py` |
| Modify token counting | `apps/api/services/usage/recorder.py` → `TokenCounter.ingest()` |
| Change vLLM API calls | `apps/api/services/vllm/client.py` |
| Add new DB table | `db/models/<name>.py` + `alembic revision` |
| Change config defaults | `apps/api/config.py` |
| Modify seeded data | `scripts/seed_dev_data.py` |
| Change mock vLLM behavior | `infra/mock-vllm/main.py` |

---

## Git Workflow

1. Work on a feature branch: `git checkout -b feature/<name>`
2. Run integration tests before committing
3. Commit with a descriptive message referencing the phase: `feat(phase-3): add key expiry enforcement`
4. Update `STATUS.md` after completing a phase milestone

---

## Updating the Documentation

Documentation lives in `docs/`. Each document corresponds to a specific area:

- Add a new API endpoint → update `docs/api/api-reference.md`
- Change config variables → update `docs/architecture/system-architecture.md` (Configuration Reference section)
- Add a new DB table → update `docs/architecture/data-model.md`
- Change rate limit behavior → update `docs/guides/rate-limits-and-quotas.md`

After significant changes, update `STATUS.md` to reflect the new completion state.
