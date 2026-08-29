# Inferra — Master Status

> **Single source of truth.** Update this file after every work session.  
> Last updated: 2026-08-29 (clarified path to production — Stage G → integrate.sh → benchmarks → beta checklist)

---

## At a Glance

| Item | Value |
|------|-------|
| **Overall progress** | ~30% production-validated (code 100% complete; GPU validation pending) |
| **Phase 1 (GPU runtime)** | 90% — Stages A–E done; F script ready; **Stage G is the only remaining blocker** |
| **Phases 2–8 (control plane)** | Code-complete against mock vLLM — will be production-validated by `integrate.sh` after Stage G |
| **RunPod pod** | `inferra-v1-migration` · L4 24 GB · `5fmoz125ju1zc0` · **$0.50/hr** |
| **Model** | Qwen/Qwen3-4B · BF16 · 4096 ctx · vLLM 0.28.0 |
| **VRAM at idle** | 19,112 MiB / 23,034 MiB (83%) |
| **Next action** | Run Stage G on pod (`04_finalize_phase1.sh`), then `integrate.sh` on Mac |

---

## Path to Production — Four Steps

> This is the complete, ordered sequence from current state to production-grade V1.
> All code is already written. These steps connect real GPU hardware to it.

---

### Step 0 (optional but recommended): Formally close Phase 1 Stage F

Run the streaming validation script on the pod to capture a TTFT baseline before the Stage G restart.  
Skip if `/workspace/benchmarks/streaming-validation.json` already exists with `passed: true`.

```bash
# Upload to pod (if not already there)
scp scripts/runpod/05_validate_streaming.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On the pod
bash /workspace/scripts/05_validate_streaming.sh
```

Result saved to `/workspace/benchmarks/streaming-validation.json`.

---

### Step 1 — Pod: Complete Phase 1 Stage G (the only remaining blocker)

This is the single thing keeping all of Phases 2–8 from being production-validated.  
It restarts vLLM with the V1 production flags: **8K context + LoRA slots + prefix caching**.

```bash
# Upload to pod (if not already there)
scp scripts/runpod/04_finalize_phase1.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On the pod (attach to existing tmux session)
tmux attach -t inferra
bash /workspace/scripts/04_finalize_phase1.sh
```

What it does: kills 4K vLLM → restarts with 8K ctx + LoRA + prefix caching → validates streaming → captures VRAM + latency snapshots.

**Acceptance criteria:** `/health` returns 200; streaming tokens arrive incrementally; `/workspace/benchmarks/gpu-after-v1-load.txt` saved.

---

### Step 2 — Mac: Wire the full control plane to real vLLM (`integrate.sh`)

Run this immediately after Stage G completes. This is the command that production-validates all of Phases 2–8 in one shot.

```bash
# Get the current container ID from RunPod dashboard → Connect → SSH
# e.g. if SSH hostname is 5fmoz125ju1zc0-64410f27@ssh.runpod.io → container ID is 64410f27
./scripts/integrate.sh <CONTAINER_ID>
```

What it does automatically (~3 minutes):

| # | Action |
|---|--------|
| 1 | Opens SSH tunnel: Mac `:8001` → RunPod `:8000` |
| 2 | Verifies vLLM is reachable through the tunnel |
| 3 | Tears down old stack + volumes (clean start) |
| 4 | Brings up full Docker Compose stack (postgres, redis, minio, gateway, prometheus, grafana) |
| 5 | Seeds dev org + API keys |
| 6 | Seeds real worker — retires mock, points alias to real vLLM deployment |
| 7 | Runs full integration test suite (`tests/integration/`) |
| 8 | Runs baseline benchmark through the gateway |
| 9 | Prints summary: gateway URL, Grafana URL, inference + admin keys |

After this completes, the platform is live end-to-end against real GPU inference. All Phases 2–8 (auth, metering, LoRA, rate limits, observability) are production-validated.

---

### Step 3 — Mac: Run the Phase 8 benchmark suite (makes V1 production-grade)

```bash
BENCHMARK_URL=http://localhost:9100/v1/chat/completions \
API_KEY=$INFERRA_INFERENCE_KEY \
MODEL=test-assistant \
bash scripts/runpod/06_run_all_benchmarks.sh
```

Runs all 7 benchmark stages (baseline, concurrency sweep, context sweep, LoRA mix, prefix cache, overload/429 stress, Locust load test) and writes real numbers into `docs/architecture/v1-capacity-report.md`.

Then walk through `docs/runbooks/beta-checklist.md` (13 sections: TLS, secrets, alert thresholds, rate limit tuning, etc.).

---

### Step 4 — Stop the pod when done

```
RunPod dashboard → inferra-v1-migration → Stop
```

Saves $0.50/hr. The platform is production-grade at this point.

---

## Phase Status

### Phase 1 — Infrastructure & Runtime Foundation
**Milestone:** M1 | **Plan:** [`plans/phase-1-infrastructure-and-runtime.md`](plans/phase-1-infrastructure-and-runtime.md)

| Stage | Status | Notes |
|-------|--------|-------|
| A — GPU validated | ✅ Done | NVIDIA L4, CUDA, PyTorch confirmed |
| B — vLLM env created | ✅ Done | `/workspace/vllm-env`, vLLM 0.28.0 |
| C — Qwen3-4B downloaded | ✅ Done | ~8 GB in `/workspace/models/Qwen3-4B` |
| D — 4K BF16 baseline serving | ✅ Done | Port 8000, 83% VRAM (19,112 / 23,034 MiB), stable |
| E — Non-streaming API validated | ✅ Done | 200 OK, coherent response confirmed |
| F — Streaming validated | ⚠️ Script ready | `05_validate_streaming.sh` created with TTFT measurement; run on pod to formally close |
| G — V1 production flags (8K, LoRA, prefix cache) | ❌ Pending | Run `04_finalize_phase1.sh` in next pod session |
| Baseline benchmark (`baseline.py`) | ❌ Not run | Script updated (multi-profile + TTFT); run after Stage G |
| Docker Compose updated (real vLLM) | ✅ Ready | `docker-compose.real.yml` overlay created |
| Post-implementation docs filled in | ✅ Filled | VRAM snapshot + config + deviations recorded in phase plan |

**Exit criterion:** Stable streaming request from vLLM; baseline memory + latency captured.

---

### Phase 2 — FastAPI API Gateway
**Milestone:** M2 | **Plan:** [`plans/phase-2-api-gateway.md`](plans/phase-2-api-gateway.md)

| Status | ✅ Code-complete — production-validated by `integrate.sh` (Step 2) |
|--------|---------------------------------------------------------------------|
| What's built | OpenAI-compatible proxy, streaming SSE pass-through, `ResolvedInferenceTarget` routing |
| Validated by | `integrate.sh` step 7 (integration tests) + step 8 (baseline benchmark through gateway) |

---

### Phase 3 — Identity & Authentication
**Milestone:** M3 | **Plan:** [`plans/phase-3-identity-and-authentication.md`](plans/phase-3-identity-and-authentication.md)

| Status | ✅ Code-complete — production-validated by `integrate.sh` (Step 2) |
|--------|---------------------------------------------------------------------|
| What's built | PostgreSQL schema, org/tenant management, API key create/hash/revoke, auth middleware, admin vs inference key separation |
| Validated by | `integrate.sh` step 5 (seed org + keys) + step 7 (`test_auth.py` integration tests) |

---

### Phase 4 — Usage Metering & Request Tracing
**Milestone:** M4 | **Plan:** [`plans/phase-4-usage-metering-and-tracing.md`](plans/phase-4-usage-metering-and-tracing.md)

| Status | ✅ Code-complete — production-validated by `integrate.sh` (Step 2) |
|--------|---------------------------------------------------------------------|
| What's built | `request_id` per call, durable `requests`/`usage_metrics` records, full latency decomposition (gateway/routing/queue/ttft/decode), `GET /v1/usage` endpoint |
| Validated by | `integrate.sh` step 7 (`test_usage.py`) — real token counts from vLLM replace synthetic mock numbers |

---

### Phase 5 — LoRA Adapter System
**Milestone:** M5 | **Plan:** [`plans/phase-5-lora-adapter-system.md`](plans/phase-5-lora-adapter-system.md)

| Status | ✅ Code-complete — production-validated by `integrate.sh` (Step 2) after Stage G |
|--------|-----------------------------------------------------------------------------------|
| What's built | Adapter registry (Postgres + MinIO), state machine (`REGISTERED → AVAILABLE → ACTIVE`), model alias resolution, `lora_request` wiring to vLLM, tenant isolation |
| Validated by | `integrate.sh` step 7 (`test_adapters.py`) — requires Stage G (LoRA flags enabled in vLLM) |

---

### Phase 6 — Rate Limits & Admission Control
**Milestone:** M6 | **Plan:** [`plans/phase-6-rate-limits-and-admission-control.md`](plans/phase-6-rate-limits-and-admission-control.md)

| Status | ✅ Code-complete — production-validated by `integrate.sh` + Phase 8 benchmarks |
|--------|---------------------------------------------------------------------------------|
| What's built | Redis token-bucket RPM limits, concurrent request caps, input/output token ceilings, daily/monthly quotas, 429/503 with actionable messages, queue depth admission control |
| Validated by | `integrate.sh` step 7 (`test_rate_limits.py`) + `overload.py` benchmark (Step 3) confirms 429/503 pattern under real traffic |

---

### Phase 7 — Observability Stack
**Milestone:** M7 | **Plan:** [`plans/phase-7-observability-stack.md`](plans/phase-7-observability-stack.md)

| Status | ✅ Code-complete — production-validated by `integrate.sh` (Step 2) |
|--------|---------------------------------------------------------------------|
| What's built | Prometheus counters/gauges/histograms, vLLM native metric scraping, GPU utilization via nvidia-smi exporter, Grafana dashboards, OpenTelemetry spans gateway→vLLM |
| Validated by | Post-`integrate.sh`: `http://localhost:9090/targets` shows gateway + vLLM both UP; Grafana panels show live GPU data |

---

### Phase 8 — Benchmarking & Beta Hardening
**Milestone:** M8 + M9 | **Plan:** [`plans/phase-8-benchmarking-and-beta.md`](plans/phase-8-benchmarking-and-beta.md)

| Status | ✅ Code-complete — benchmark numbers pending real GPU run (Step 3) |
|--------|-------------------------------------------------------------------|
| What's built | Full 6-script benchmark suite, `06_run_all_benchmarks.sh` orchestrator, 4-task Locust load test (with streaming), TLS nginx config, pinned `versions.env` + `requirements.txt`, full admin UI at `/admin`, `beta-checklist.md` |
| Triggered by | Stage G complete → `integrate.sh` passes → run `06_run_all_benchmarks.sh` → fill `v1-capacity-report.md` |
| Final gate | Walk `docs/runbooks/beta-checklist.md` (13 sections) → platform is production-grade V1 |

---

## RunPod Pod Reference

| Field | Value |
|-------|-------|
| Pod name | `inferra-v1-migration` |
| Pod ID | `5fmoz125ju1zc0` |
| GPU | NVIDIA L4 24 GB |
| Cost | $0.50/hr (GPU) while RUNNING |
| Second pod | `inferra-v1` (`hz8xfdepg96xkg`) — paused, $0.01/hr storage only |
| SSH command | `ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io` |
| Note | Container ID suffix changes on every restart — get fresh from dashboard |

**Load SSH key once per Mac session:**
```bash
ssh-add ~/.ssh/id_ed25519_runpod
```

**Workspace layout (persistent `/workspace`):**
```
/workspace/
├── scripts/          ← runbook scripts (03_validate_api.sh recreated 2026-08-29)
├── vllm-env/         ← vLLM 0.28.0 Python venv
├── vllm-version.txt
├── huggingface-cache/
├── models/Qwen3-4B/  ← ~8 GB weights
├── adapters/
├── benchmarks/       ← gpu snapshots, test-non-streaming.json
├── logs/
└── inference-platform/
```

---

## Key Observations (2026-08-29)

- Non-streaming `/v1/chat/completions` → **200 OK**, model responding correctly
- Model running in **thinking mode** (Qwen3 default — `<think>` tags in response); add `"chat_template_kwargs": {"enable_thinking": false}` to suppress
- `finish_reason: "length"` at 256 tokens — increase `max_tokens` to 512–1024 for complete answers
- VRAM: 19,112 / 23,034 MiB (83%) at idle — **correct and expected** (Stage D: 4K ctx, BF16, 0.85 util)
- GPU Util: 0% at idle → spikes to ~100% during generation → drops back — **correct and expected**
- **Phase 1 Stage F**: `05_validate_streaming.sh` created — Python-based TTFT measurement; run on pod to formally close Stage F
- **Phase 1 Stage G is the only remaining blocker**: once `04_finalize_phase1.sh` runs, `integrate.sh` can production-validate all of Phases 2–8 in one shot
- **Phases 2–8 require no code changes** to work against real vLLM — `resolver.py`, `rate_limiter.py`, `recorder.py`, `registry.py` all work as-is; only the vLLM endpoint URL changes (mock → SSH tunnel)
- **Phase 8 code-complete**: all 6 benchmark scripts + `06_run_all_benchmarks.sh` + Locust 4-task suite + nginx TLS + pinned versions ready
- **requirements.txt** pinned to exact versions for beta tag (was `>=` ranges)
- **Documentation suite**: 19 documents covering the entire product end-to-end. See [`docs/README.md`](docs/README.md).

---

## Integration Architecture

```
Mac (local)
├── docker compose -f docker-compose.yml -f docker-compose.real.yml
│   ├── mock-vllm         ← health stub only (passes compose healthcheck)
│   ├── api-gateway :9100 ← VLLM_BASE_URL=http://host.docker.internal:8001
│   ├── postgres
│   ├── redis
│   ├── minio
│   ├── prometheus
│   └── grafana :3000
│
│   SSH tunnel (port forward)
│   localhost:8001 ──────────────────────────────► RunPod:8000
│                                                    └── vLLM serving Qwen3-4B
│
└── Worker.endpoint in DB = http://host.docker.internal:8001
    (per-request routing in chat.py uses this, not VLLM_BASE_URL)
```

**Key files for integration:**

| File | Purpose |
|------|---------|
| `docker-compose.real.yml` | Compose overlay — sets VLLM_BASE_URL to tunnel endpoint |
| `scripts/runpod/04_finalize_phase1.sh` | Run on pod: Stage G restart, streaming test, baseline capture |
| `scripts/integrate.sh` | Run on Mac: tunnel + stack + seed + test (one command) |
| `scripts/seed_real_worker.py` | Retires mock worker, seeds real RunPod worker, re-points alias |

---

## Document Map

### Master Docs
| Document | Purpose |
|----------|---------|
| **`STATUS.md`** (this file) | Single source of truth — progress, next steps, pod reference |
| [`README.md`](README.md) | Quick start + full feature overview + links to all docs |
| [`docs/README.md`](docs/README.md) | Documentation index — navigate the full doc set |

### Product & Architecture
| Document | Purpose |
|----------|---------|
| [`docs/product/overview.md`](docs/product/overview.md) | What Inferra is, capabilities, V1 scope, tech stack |
| [`docs/architecture/system-architecture.md`](docs/architecture/system-architecture.md) | Components, request lifecycle, config reference |
| [`docs/architecture/data-model.md`](docs/architecture/data-model.md) | PostgreSQL schema, Redis layout, MinIO layout |
| [`docs/architecture/v1-capacity-report.md`](docs/architecture/v1-capacity-report.md) | VRAM + KV-cache capacity calculations (fill in after Phase 8) |

### API Reference
| Document | Purpose |
|----------|---------|
| [`docs/api/api-reference.md`](docs/api/api-reference.md) | Every endpoint — request/response schemas, curl examples, error tables |
| [`docs/api/authentication.md`](docs/api/authentication.md) | API key types, auth flow, create/revoke, security best practices |

### How-To Guides
| Document | Purpose |
|----------|---------|
| [`docs/guides/getting-started.md`](docs/guides/getting-started.md) | First inference call in 5 minutes (local stack) |
| [`docs/guides/lora-adapters.md`](docs/guides/lora-adapters.md) | Register, upload, and serve LoRA adapters |
| [`docs/guides/rate-limits-and-quotas.md`](docs/guides/rate-limits-and-quotas.md) | Admission controls, Redis internals, retry strategy |
| [`docs/guides/observability.md`](docs/guides/observability.md) | Prometheus metrics reference, Grafana, OTel tracing |

### Deployment
| Document | Purpose |
|----------|---------|
| [`docs/deployment/local-development.md`](docs/deployment/local-development.md) | Docker Compose, mock vLLM, dev commands, port reference |
| [`docs/deployment/runpod-gpu.md`](docs/deployment/runpod-gpu.md) | NVIDIA L4 setup, vLLM serve, SSH tunnel, integrate.sh |

### Development
| Document | Purpose |
|----------|---------|
| [`docs/development/contributing.md`](docs/development/contributing.md) | Dev setup, code conventions, integration tests, benchmark scripts |

### Phase Plans
| Document | Purpose |
|----------|---------|
| [`plans/README.md`](plans/README.md) | Phase overview, dependency graph, tech stack |
| [`plans/phase-1-infrastructure-and-runtime.md`](plans/phase-1-infrastructure-and-runtime.md) | GPU runtime — detailed implementation steps |
| [`plans/phase-2-api-gateway.md`](plans/phase-2-api-gateway.md) | FastAPI gateway plan |
| [`plans/phase-3-identity-and-authentication.md`](plans/phase-3-identity-and-authentication.md) | Auth + tenancy plan |
| [`plans/phase-4-usage-metering-and-tracing.md`](plans/phase-4-usage-metering-and-tracing.md) | Metering + tracing plan |
| [`plans/phase-5-lora-adapter-system.md`](plans/phase-5-lora-adapter-system.md) | LoRA adapter registry plan |
| [`plans/phase-6-rate-limits-and-admission-control.md`](plans/phase-6-rate-limits-and-admission-control.md) | Rate limits plan |
| [`plans/phase-7-observability-stack.md`](plans/phase-7-observability-stack.md) | Observability plan |
| [`plans/phase-8-benchmarking-and-beta.md`](plans/phase-8-benchmarking-and-beta.md) | Benchmarking + beta plan |

### Runbooks (Operations)
| Document | Purpose |
|----------|---------|
| [`docs/runbooks/runpod-poc-runbook.md`](docs/runbooks/runpod-poc-runbook.md) | Step-by-step pod operation (SSH, tmux, scripts, GPU verification) |
| [`docs/runbooks/first-l4-deployment.md`](docs/runbooks/first-l4-deployment.md) | First-time provisioning runbook |
| [`docs/runbooks/beta-checklist.md`](docs/runbooks/beta-checklist.md) | Beta readiness checklist |
