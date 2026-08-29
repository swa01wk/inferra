# How to Run All Phases — End-to-End Execution Guide

> **Single command on each machine.** This document covers everything needed to go from a live RunPod pod to a fully operational Inferra V1 platform with real GPU inference.

**Pod reference**

| Field | Value |
|-------|-------|
| Pod name | `inferra-v1-migration` |
| Pod ID | `5fmoz125ju1zc0` |
| GPU | NVIDIA L4 24 GB |
| Cost | $0.50 / hr while running |
| SSH command | `ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io` |
| Container ID | Read from RunPod dashboard → Connect → SSH (e.g. `64410f27`) |

> **Related runbooks:**
> - [`runpod-poc-runbook.md`](runpod-poc-runbook.md) — SSH setup, tmux cheat sheet, GPU verification,
>   manual vLLM commands, and storage layout. Read this first if you are new to the pod.
> - [`first-l4-deployment.md`](first-l4-deployment.md) — First-time pod provisioning (already done).
> - [`beta-checklist.md`](beta-checklist.md) — Checklist to verify before inviting beta users.

---

## Prerequisites (one-time)

```bash
# Load SSH key once per Mac session
ssh-add ~/.ssh/id_ed25519_runpod

# Confirm Docker Desktop is running
docker info
```

---

## Step 0 — Phase 1 Stage F: Streaming Validation (new — run before Stage G)

> **When to do this:** vLLM is running with Stage D/E flags (4K baseline). Run this to
> formally close Stage F with a TTFT measurement before the Stage G restart.
>
> Skip if `/workspace/benchmarks/streaming-validation.json` exists and shows `passed: true`.

Upload the script to the pod, then run it in your SSH session:

```bash
# On Mac — upload
scp scripts/runpod/05_validate_streaming.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On pod — run
bash /workspace/scripts/05_validate_streaming.sh
```

Expected output:
```
  TTFT              : 312.4 ms
  Completion tokens : 96
  Incremental       : True
  STAGE F PASSED ✓
Results saved to: /workspace/benchmarks/streaming-validation.json
```

---

## Step 1 — Phase 1 Stage G: V1 Production vLLM Restart (on the pod)

> **When to do this:** vLLM is already running with the 4K baseline config. Stage G restarts it
> with the V1 production flags (8K context, LoRA slots, prefix caching) and captures baseline metrics.
>
> Skip if Stage G is already complete — check `/workspace/benchmarks/gpu-after-v1-load.txt`.
>
> **New to SSH / tmux?** See [`runpod-poc-runbook.md §§ 1–2 and 5`](runpod-poc-runbook.md) for
> the full SSH setup, how to get a fresh container ID after a restart, and the tmux key cheat sheet.

Upload and run on the pod:

```bash
# On Mac — upload (if not already on pod)
scp scripts/runpod/04_finalize_phase1.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On pod
tmux attach -t inferra
bash /workspace/scripts/04_finalize_phase1.sh
```

### What `04_finalize_phase1.sh` does

1. Kills the current 4 K vLLM process.
2. Starts vLLM in a new tmux window (`vllm-v1`) with V1 production flags:
   - `--max-model-len 8192`
   - `--gpu-memory-utilization 0.90`
   - `--enable-lora --max-lora-rank 16 --max-loras 4`
   - `--enable-prefix-caching`
3. Waits for `/health` to return 200 (up to 3 minutes).
4. Runs a non-streaming completion test and a streaming test.
5. Captures VRAM state (`/workspace/benchmarks/gpu-after-v1-load.txt`).
6. Captures a single-request latency snapshot (`/workspace/benchmarks/latency-snapshot-v1.json`).

### Acceptance criteria

```
✅ vLLM /health returns 200
✅ Streaming curl returns incremental tokens
✅ VRAM snapshot saved to /workspace/benchmarks/
✅ KV cache size reflects 8192 context (expect ~5.8 GiB vs ~2.9 GiB at 4096)
```

---

## Step 2 — Full Integration: Mac Control Plane → RunPod vLLM

> **Run this on your Mac** (new terminal, not the pod SSH session).
>
> Get the container ID from the SSH hostname — the part between the pod ID and `@ssh.runpod.io`.
> Example: `5fmoz125ju1zc0-64410f27@ssh.runpod.io` → container ID is `64410f27`.

```bash
cd /Users/swa/Desktop/inferra
./scripts/integrate.sh 64410f27
```

### What `integrate.sh` does (automated, ~3 minutes)

| Step | Action |
|------|--------|
| 1 | Ensures SSH key is loaded |
| 2 | Opens SSH tunnel: Mac `:8001` → RunPod `:8000` |
| 3 | Verifies vLLM is reachable through the tunnel |
| 4 | Tears down any old stack and volumes (clean start) |
| 5 | Brings up full Docker Compose stack (postgres, redis, minio, api-gateway, prometheus, grafana) |
| 6 | Seeds dev org + API keys via `scripts/seed_dev_data.py` |
| 7 | Seeds real RunPod worker via `scripts/seed_real_worker.py` (retires mock, sets `worker.endpoint = http://host.docker.internal:8001`) |
| 8 | Runs integration test suite (`tests/integration/`) |
| 9 | Runs baseline benchmark through the gateway (`scripts/benchmark/baseline.py`) |
| 10 | Prints summary with all URLs and API keys |

### Environment variables (override defaults if needed)

```bash
SSH_KEY=~/.ssh/id_ed25519_runpod   # default
INFERRA_BASE_URL=http://localhost:9100  # used by tests
```

### Acceptance criteria

```
✅ SSH tunnel open (lsof -i tcp:8001 shows a process)
✅ docker compose ps — all services healthy
✅ Seed output shows: org created, inference key, admin key
✅ Integration tests pass (0 failures)
✅ Baseline benchmark prints TTFT and tokens/s numbers
```

---

## Step 3 — Access the Running Platform

After `integrate.sh` completes, these are all live:

| Service | URL | Credentials |
|---------|-----|-------------|
| **Inferra Gateway** | `http://localhost:9100` | Use inference key from seed output |
| **Admin UI** | `http://localhost:9100/admin` | Use admin key from seed output |
| **Grafana** | `http://localhost:3000` | admin / admin |
| **Prometheus** | `http://localhost:9090` | — |
| **Metrics** | `http://localhost:9100/metrics` | — |
| **API docs** | `http://localhost:9100/docs` | — |

### Quick smoke test

```bash
export INFERRA_KEY=<inference-key-from-seed-output>

# Health check
curl http://localhost:9100/health

# Chat completion
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "stream": false,
    "max_tokens": 64
  }'

# Streaming
curl -N http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [{"role": "user", "content": "Count from 1 to 5."}],
    "stream": true,
    "max_tokens": 64
  }'
```

---

## Step 4 — Run the Phase 8 Benchmark Suite

### Option A: One-command orchestrator (recommended)

```bash
# All 7 stages in sequence → auto-generates docs/architecture/v1-capacity-report.md
BENCHMARK_URL=http://localhost:9100/v1/chat/completions \
API_KEY=$INFERRA_INFERENCE_KEY \
MODEL=test-assistant \
bash scripts/runpod/06_run_all_benchmarks.sh
```

### Option B: Run stages individually

```bash
export INFERRA_BASE_URL=http://localhost:9100
export URL=$INFERRA_BASE_URL/v1/chat/completions

# Stage 1 — Single-request baseline (4 profiles + streaming TTFT)
python scripts/benchmark/baseline.py --url $URL --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/baseline.json

# Stage 2 — Concurrency sweep 1→2→4→8→16
python scripts/benchmark/concurrency.py --url $URL --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/concurrency.json

# Stage 3 — Context sweep 2K/4K/8K at c=1,4,8
python scripts/benchmark/context_sweep.py --url $URL --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/context_sweep.json

# Stage 4 — LoRA mix (skip if no active adapter registered)
# python scripts/benchmark/lora_mix.py --url $URL --api-key $INFERRA_INFERENCE_KEY \
#     --lora-alias lora-assistant --output /workspace/benchmarks/lora_mix.json

# Stage 5 — Prefix cache effectiveness (2K shared prefix)
python scripts/benchmark/prefix_cache.py --url $URL --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/prefix_cache.json

# Stage 7 — Overload + admission control stress
python scripts/benchmark/overload.py --url $URL --api-key $INFERRA_INFERENCE_KEY \
    --output /workspace/benchmarks/overload.json

# Aggregate all results → capacity report
python scripts/benchmark/report.py \
    --baseline /workspace/benchmarks/baseline.json \
    --concurrency /workspace/benchmarks/concurrency.json \
    --context /workspace/benchmarks/context_sweep.json \
    --prefix /workspace/benchmarks/prefix_cache.json \
    --overload /workspace/benchmarks/overload.json \
    --report-path docs/architecture/v1-capacity-report.md
```

### Locust load test

```bash
# Install locust if not already: pip install locust==2.32.2
locust -f tests/load/locustfile.py \
  --host http://localhost:9100 \
  --api-key $INFERRA_INFERENCE_KEY \
  --headless \
  -u 10 -r 2 \
  --run-time 60s \
  --html reports/load-test.html
```

Task mix: short/medium/streaming/long requests (weights: 3/2/2/1).

---

## Step 5 — Run Integration Tests Standalone

```bash
export INFERRA_BASE_URL=http://localhost:9100
export INFERRA_INFERENCE_KEY=<inference-key>
export INFERRA_ADMIN_KEY=<admin-key>

# Run all integration tests
pytest tests/integration/ -v

# Run only a specific suite
pytest tests/integration/test_auth.py -v
pytest tests/integration/test_inference.py -v
pytest tests/integration/test_adapters.py -v
pytest tests/integration/test_rate_limits.py -v
pytest tests/integration/test_usage.py -v
pytest tests/integration/test_health.py -v

# Skip slow rate-limit tests
pytest tests/integration/ -v -m "not rate_limits"
```

---

## Step 6 — Stop the Pod When Done

**Critical:** The pod costs $0.50/hr while running. Always stop it when done.

```
RunPod dashboard → inferra-v1-migration → Stop
```

Or SSH in and confirm before stopping:

```bash
# Check vLLM is still healthy before stopping
curl http://localhost:8000/health

# Stop all local services (Mac)
docker compose down
```

---

## Resume from Here (next session)

> See also [`runpod-poc-runbook.md § 6 — Resume After Restart`](runpod-poc-runbook.md) for the
> full pod-side resume sequence (GPU re-validation, vLLM restart, manual quick-start commands).

At the start of each new session:

```bash
# 1. Start the pod: RunPod dashboard → Start

# 2. Get new container ID from dashboard → Connect → SSH
#    Example: 5fmoz125ju1zc0-NEWCONTAINERID@ssh.runpod.io

# 3. SSH in and check vLLM is still running
ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<NEW_CONTAINER_ID>@ssh.runpod.io
tmux attach -t inferra    # vLLM should be in window 'vllm-v1'
curl http://localhost:8000/health

# 4. If vLLM is down (container restarted), restart it:
bash /workspace/scripts/04_finalize_phase1.sh

# 5. Re-run integrate.sh with the new container ID
cd /Users/swa/Desktop/inferra
./scripts/integrate.sh <NEW_CONTAINER_ID>
```

> **Note:** The container ID suffix changes on every pod restart. Always read it fresh from the RunPod dashboard.

---

## Architecture Reference

```
Mac (local)
├── docker compose -f docker-compose.yml -f docker-compose.real.yml
│   ├── api-gateway :9100      ← VLLM_BASE_URL=http://host.docker.internal:8001
│   ├── postgres               ← all platform state (orgs, keys, adapters, usage)
│   ├── redis                  ← rate limits, concurrency counters, daily quotas
│   ├── minio                  ← LoRA adapter artifact storage
│   ├── prometheus             ← metrics scraping (gateway + vLLM)
│   └── grafana :3000          ← dashboards
│
│   SSH tunnel (background process)
│   localhost:8001 ──────────────────────────────► RunPod:8000
│                                                    └── vLLM → Qwen3-4B on L4
│
└── Worker.endpoint in DB = http://host.docker.internal:8001
    (resolver.py routes each request to this endpoint)
```

---

## Troubleshooting

> For GPU-specific issues (VRAM reading, confirming vLLM owns the GPU, PyTorch CUDA checks),
> see [`runpod-poc-runbook.md § 7 — Verifying GPU Is Actually Being Used`](runpod-poc-runbook.md).

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `integrate.sh` fails at "vLLM not reachable" | Tunnel not open / wrong container ID | Check container ID in dashboard; re-run with correct ID |
| `api-gateway` exits at startup | Postgres not ready | Wait for `docker compose ps` to show postgres healthy; re-run |
| 503 on all inference requests | vLLM unreachable through tunnel | Check SSH tunnel: `lsof -i tcp:8001`; re-open if missing |
| 401 on all requests | Keys not seeded | Run `python scripts/seed_dev_data.py` again |
| `tmux: no server running` on pod | vLLM was run outside tmux | Run `bash /workspace/scripts/04_finalize_phase1.sh` to restart in tmux |
| vLLM OOM on 8K restart | VRAM headroom too tight | Lower `--gpu-memory-utilization 0.87` in `04_finalize_phase1.sh` |
| Grafana shows no data | Prometheus not scraping | Check `http://localhost:9090/targets` — both gateway and vllm must be UP |
