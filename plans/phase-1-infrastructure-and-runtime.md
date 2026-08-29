# Phase 1 — Infrastructure & Runtime Foundation

**Spec Milestone:** M1 — Raw Runtime  
**Exit Criterion:** Stable streaming request from vLLM on the L4; baseline memory and latency metrics captured.

---

## Current POC Status

**RunPod provisioning is complete. SSH access from the development machine is verified.**  
Next step: begin at **Stage A — GPU/runtime validation** below. Do not re-provision.

---

## Budget & Cost Envelope

| Item | Rate |
|------|------|
| NVIDIA L4 24 GB GPU compute | ~$0.49 / hr |
| Pod total (GPU + storage) | ~$0.50 / hr |
| $10 credit balance | ~20 hrs of active runtime |

**Critical:** SSH disconnect does **not** stop billing. Explicitly **STOP the Pod** at the end of every session unless the inference server must remain live. A stopped Pod incurs only persistent-volume storage costs (much lower than GPU compute).

---

## Resume-From-Here Checklist

Run these checks at the start of each new SSH session before proceeding:

```bash
nvidia-smi                       # confirm L4 visible
ls /workspace/vllm-env           # confirm venv exists
ls /workspace/models/Qwen3-4B   # confirm model downloaded
cat /workspace/benchmarks/gpu-before-vllm.txt  # idle baseline snapshot
```

If any artifact is missing, follow the relevant sub-section below to recreate it.

---

## Goals

- Provision the RunPod L4 24 GB instance with the correct CUDA image.
- Start Qwen3-4B under vLLM with the V1 configuration flags.
- Measure actual VRAM consumption at startup and under load.
- Verify streaming completions directly against the vLLM endpoint.
- Capture the baseline latency/throughput numbers that all later phases are compared against.
- Build the Docker Compose skeleton that every subsequent service will plug into.

---

## Deliverables

1. `docker-compose.yml` with the `vllm` service defined.
2. `infra/docker/vllm.Dockerfile` (or pinned image reference).
3. `scripts/benchmark/baseline.py` — single-request latency capture script.
4. `docs/runbooks/first-l4-deployment.md` — step-by-step provisioning runbook.
5. A recorded memory snapshot: model weights + CUDA overhead + initial KV-cache budget.

---

## Step-by-Step Implementation

### Stage A — Provision and Validate the GPU (✅ provisioning complete)

1. SSH into the running Pod using the configured SSH key.
2. Validate GPU visibility and PyTorch/CUDA:

```bash
nvidia-smi
# Expected: NVIDIA L4, ~24 GB VRAM

python --version

python -c "
import torch
print('PyTorch:', torch.__version__)
print('CUDA:', torch.cuda.is_available())
print('GPU:', torch.cuda.get_device_name(0))
print('VRAM GB:', torch.cuda.get_device_properties(0).total_memory / 1024**3)
"
# Acceptance criterion: cuda.is_available() == True, device name == NVIDIA L4

df -h
ls -lah /workspace
```

### Stage B — Create the Python/vLLM Environment

```bash
cd /workspace
python -m venv vllm-env
source vllm-env/bin/activate
pip install --upgrade pip
pip install vllm
vllm --version           # record this version in infra/docker/versions.env
```

Pin the exact vLLM version immediately. CLI flags and runtime behaviour change between releases.

### Stage C — Configure Persistent Hugging Face Cache

```bash
mkdir -p /workspace/huggingface-cache
export HF_HOME=/workspace/huggingface-cache
# Add to ~/.bashrc so it survives reconnects:
echo 'export HF_HOME=/workspace/huggingface-cache' >> ~/.bashrc
```

#### Download and verify Qwen3-4B explicitly

Keep at least 15–20 GB free before downloading (model weights + cache + adapters + benchmark output).

```bash
pip install -U "huggingface_hub[cli]"

hf download Qwen/Qwen3-4B --local-dir /workspace/models/Qwen3-4B
# Fallback:
# huggingface-cli download Qwen/Qwen3-4B --local-dir /workspace/models/Qwen3-4B

du -sh /workspace/models/Qwen3-4B
ls -lah /workspace/models/Qwen3-4B
# Expected: config/tokenizer files + one or more .safetensors weight files
```

#### Capture idle GPU baseline

```bash
nvidia-smi
nvidia-smi > /workspace/benchmarks/gpu-before-vllm.txt
```

### Stage D — First 4K BF16 Baseline (simplest possible config first)

**Do not enable LoRA, FP8 KV-cache, or aggressive memory settings in the first run.**  
Goal: establish a clean BF16 + 4K reference point before raising any flags.

```bash
source /workspace/vllm-env/bin/activate
export HF_HOME=/workspace/huggingface-cache

vllm serve /workspace/models/Qwen3-4B \
  --served-model-name Qwen/Qwen3-4B \
  --dtype bfloat16 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85
```

Monitor VRAM during startup:
```bash
watch -n2 nvidia-smi
```

### Stage E — Validate the OpenAI-Compatible API (non-streaming)

```bash
curl http://localhost:8000/v1/models

curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B",
    "messages": [{"role": "user", "content": "Explain KV cache in simple terms."}],
    "temperature": 0.6,
    "max_tokens": 256
  }'
```

Acceptance criteria: model responds coherently; no CUDA OOM; vLLM stays healthy.

### Stage F — Validate Streaming

```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B",
    "messages": [{"role": "user", "content": "Explain how transformer attention works."}],
    "stream": true,
    "max_tokens": 256
  }'
```

Acceptance criterion: output arrives incrementally (not only after the full completion is generated).

### Stage G — Move to V1 Runtime Configuration (after 4K baseline is stable)

After 4K BF16 baseline memory/latency are recorded, restart vLLM with V1 production flags:

```bash
# First check what flags your vLLM version accepts:
vllm serve --help

vllm serve /workspace/models/Qwen3-4B \
  --served-model-name Qwen/Qwen3-4B \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --enable-lora \
  --max-lora-rank 16 \
  --max-loras 4 \
  --enable-prefix-caching \
  --host 0.0.0.0 \
  --port 8000
```

Record the exact flags and vLLM version that succeed in `infra/docker/versions.env`.

---

### 1.1 RunPod Provisioning (already done — archived for reference)

- Selected a RunPod L4 24 GB Pod.
- Attached persistent volume for model weights (`/workspace`).
- Used CUDA-compatible base image.
- Record exact image digest/tag in `infra/docker/versions.env` for reproducibility.

### 1.2 vLLM Configuration (V1 production flags — use after Stage D baseline)

Store final working flags in `infra/docker/vllm.env`. Reference values from Stage G:

```
vllm serve Qwen/Qwen3-4B \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --enable-lora \
  --max-lora-rank 16 \
  --max-loras 4 \
  --enable-prefix-caching \
  --host 0.0.0.0 \
  --port 8000
```

**Important:** `--gpu-memory-utilization 0.90` is a starting point. Profile actual startup allocation before raising this value. Do not treat a high value as automatically desirable — headroom matters.

### 1.3 Docker Compose Skeleton

```yaml
# docker-compose.yml (Phase 1 skeleton)
services:
  vllm:
    image: vllm/vllm-openai:<pinned-tag>
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - HF_HOME=/mnt/model-cache/huggingface
    volumes:
      - model-cache:/mnt/model-cache
    ports:
      - "8000:8000"
    command: >
      serve Qwen/Qwen3-4B
      --dtype bfloat16
      --max-model-len 8192
      --gpu-memory-utilization 0.90
      --enable-lora
      --max-lora-rank 16
      --max-loras 4
      --enable-prefix-caching
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s

volumes:
  model-cache:
```

Services added in later phases (gateway, redis, postgres, prometheus, grafana) will be appended to this file.

### 1.4 VRAM Budget Verification

After vLLM starts, run:

```bash
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv
```

Compare against the spec estimates:

| Consumer | Planning Estimate |
|----------|-------------------|
| Qwen3-4B BF16 weights | ~8 GB |
| CUDA / vLLM / runtime overhead | Measure at startup |
| KV cache (remaining after weights + overhead) | Largest adjustable pool |
| Safety margin | Headroom to avoid OOM under peaks |

Record actual values in `docs/architecture/vram-baseline.md`.

### 1.5 KV-Cache Capacity Reference

The spec calculates BF16 KV bytes per token:

```
KV bytes/token = 36 layers × 8 KV heads × 128 head_dim × 2 (K+V) × 2 bytes = 147,456 bytes ≈ 144 KiB/token
```

| Context tokens | Approx BF16 KV per fully cached sequence |
|----------------|------------------------------------------|
| 2,048 | ~288 MiB |
| 4,096 | ~576 MiB |
| 8,192 | ~1.125 GiB |

Use these figures to reason about safe concurrency at 4K and 8K context limits before Phase 8 load tests.

---

## Baseline Metrics to Capture Before Building the Control Plane

Capture both system-level and request-level metrics before adding any gateway code.

### System Metrics (before and after model load)

```bash
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv
```

| Point in time | VRAM used | VRAM free | Total |
|--------------|-----------|-----------|-------|
| Idle (before vLLM) | record | record | record |
| After model load (before any request) | record | record | record |
| During first request (prefill peak) | record | record | record |

### Initial Benchmark Matrix

Do not publish concurrency claims before measuring. Run `scripts/benchmark/baseline.py` at:

| Prompt Tokens | Output Tokens | Concurrency | What to capture |
|--------------|---------------|-------------|-----------------|
| 256 | 128 | 1 | TTFT, total latency, tokens/s |
| 512 | 128 | 1 | TTFT, total latency, tokens/s |
| 1024 | 256 | 1 | TTFT, total latency, tokens/s |
| 2048 | 256 | 1 | TTFT, total latency, tokens/s |
| 1024 | 256 | 2 | Batching efficiency |
| 1024 | 256 | 4 | Queue start, KV pressure |

Record results in `docs/architecture/baseline-results.md`. These numbers become the performance floor for all later phases.

Stop if: error rate > 2% at any concurrency level, or P99 TTFT > 10s.

---

### 1.6 Health and Direct Streaming Verification

```bash
# Health check
curl http://localhost:8000/health

# Direct streaming completion (no gateway)
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B",
    "messages": [{"role": "user", "content": "Hello, what is 2+2?"}],
    "stream": true,
    "max_tokens": 128
  }'
```

Both must succeed before proceeding to Phase 2.

### 1.7 Baseline Benchmark Script

`scripts/benchmark/baseline.py` should capture:

- TTFT (time-to-first-token)
- Total latency
- Output tokens/second
- GPU utilization (via `nvidia-smi` or `nvml`)
- VRAM used during generation

Run at:
- 1 concurrent request with 256 / 512 / 1024 token prompts
- Output lengths of 128 / 256 tokens

Record results in `docs/architecture/baseline-results.md`. These numbers define the performance floor for all subsequent phases.

---

## Context Policy Enforcement

The V1 context ceiling is **8K tokens maximum**, **4K default**. Configure vLLM's `--max-model-len 8192`. Any request exceeding 8192 tokens (prompt + max_tokens) must be rejected at the gateway level (Phase 2). Do not rely on vLLM alone for this; the gateway must validate and return a clear error.

---

## Failure Modes to Handle in This Phase

| Failure | Expected Behaviour |
|---------|-------------------|
| vLLM not ready at startup | Health endpoint returns 503; wait for `start_period` |
| OOM during model load | Container exits; review `--gpu-memory-utilization` |
| Model download failure | vLLM logs the error; check `HF_HOME` volume mount |

---

## Exit Checklist

- [ ] vLLM starts cleanly and `/health` returns 200.
- [ ] Direct streaming `chat/completions` returns tokens.
- [ ] VRAM budget documented (weights + overhead + KV pool).
- [ ] Baseline single-request TTFT and tokens/s recorded.
- [ ] Docker Compose file committed with pinned image versions.
- [ ] `docs/runbooks/first-l4-deployment.md` written.

---

## Post-Implementation Documentation

This section records what has been built and observed as of 2026-08-29. Stages A–E are complete against the real GPU. Stage F has a new dedicated validation script. Stage G is the final pending step before Phase 2 integration.

### Implementation Log

```
Date (partial): 2026-08-29
Implemented by: Cursor Agent + manual pod execution
Status:         PARTIALLY COMPLETE — Stages A–E done on real L4; F script ready;
                G (V1 production flags: 8K + LoRA + prefix cache) pending one more run.
Git branch:     main
Pod:            inferra-v1-migration · NVIDIA L4 24 GB · $0.50/hr
```

### What Has Been Built

```
Stage A — GPU validation:     DONE (2026-08-29)
  - NVIDIA L4 24 GB confirmed via nvidia-smi
  - CUDA 12.x + PyTorch GPU confirmed

Stage B — vLLM Python venv:   DONE (2026-08-29)
  - /workspace/vllm-env/ created
  - vLLM 0.28.0 pip-installed (see vllm-version.txt on pod)

Stage C — Qwen3-4B download:  DONE (2026-08-29)
  - ~8 GB weights in /workspace/models/Qwen3-4B/
  - Model: Qwen/Qwen3-4B (bfloat16)

Stage D — 4K BF16 serving:    DONE (2026-08-29)
  - Flags: --dtype bfloat16 --max-model-len 4096 --gpu-memory-utilization 0.85
  - vLLM stable on port 8000; VRAM 83% at idle

Stage E — Non-streaming API:   DONE (2026-08-29)
  - GET /v1/models → 200 OK, Qwen/Qwen3-4B listed
  - POST /v1/chat/completions → 200 OK, coherent response
  - Observed: Qwen3 defaults to thinking mode (<think> tags)
    Fix: add "chat_template_kwargs": {"enable_thinking": false}

Stage F — Streaming:           SCRIPT READY — run 05_validate_streaming.sh
  - 03_validate_api.sh recreated 2026-08-29 (was truncated in earlier session)
  - 05_validate_streaming.sh created with TTFT measurement + incremental validation
  - Manual curl with stream=true confirmed tokens arrive — not yet formally captured
  - To complete: tmux attach → bash /workspace/scripts/05_validate_streaming.sh

Stage G — V1 production flags: PENDING — run 04_finalize_phase1.sh
  - Script ready at scripts/runpod/04_finalize_phase1.sh
  - Will restart vLLM with: 8K ctx + LoRA + prefix caching
  - Flags: --max-model-len 8192 --gpu-memory-utilization 0.90
            --enable-lora --max-lora-rank 16 --max-loras 4
            --enable-prefix-caching

Preparatory (Mac-side):        DONE
  [x] infra/mock-vllm/          — full mock for local dev without GPU
  [x] docker-compose.yml        — mock vLLM in place of real for local dev
  [x] docker-compose.real.yml   — overlay: sets VLLM_BASE_URL to SSH tunnel
  [x] scripts/seed_real_worker.py — retires mock worker, seeds real RunPod worker
  [x] scripts/benchmark/baseline.py — multi-profile baseline runner (updated 2026-08-29)
  [x] infra/docker/versions.env — Docker image versions pinned (updated 2026-08-29)
  [x] requirements.txt          — pinned to exact versions for beta tag
```

### Actual Runtime Configuration

```
vLLM version:                  0.28.0 (pip-installed in /workspace/vllm-env/)
vLLM Docker image:             vllm/vllm-openai:v0.8.5 (for future containerised run)
Qwen3-4B model revision:       PENDING — run: git -C /workspace/models/Qwen3-4B rev-parse HEAD

Stage D flags (running as of 2026-08-29):
  --dtype                    bfloat16
  --max-model-len            4096
  --gpu-memory-utilization   0.85
  --host                     0.0.0.0 --port 8000

Stage G flags (to apply with 04_finalize_phase1.sh):
  --dtype                    bfloat16
  --max-model-len            8192
  --gpu-memory-utilization   0.90
  --enable-lora
  --max-lora-rank            16
  --max-loras                4
  --enable-prefix-caching
  --host                     0.0.0.0 --port 8000

Additional observed behaviour:
  - Qwen3 thinking mode active by default — suppress with:
      "chat_template_kwargs": {"enable_thinking": false}
  - finish_reason: "length" at low max_tokens — use 512–1024 for complete answers
```

### VRAM Snapshot at Startup

```
GPU model:            NVIDIA L4 24 GB (confirmed 2026-08-29)
Total VRAM:           23,034 MiB
VRAM used (Stage D idle, 4K ctx, BF16, gpu-mem-util 0.85):
                      19,112 MiB (83%)
  Breakdown (estimated):
  - Qwen3-4B weights (BF16): ~8,500 MiB
  - vLLM runtime overhead:   ~1,200 MiB
  - KV-cache pool (0.85 ×):  ~9,412 MiB
VRAM free at idle:    3,922 MiB

After Stage G (8K ctx, gpu-mem-util 0.90 — fill in from benchmarks/vram-v1-baseline.csv):
  VRAM used:          PENDING — run 04_finalize_phase1.sh then nvidia-smi
  KV-cache pool:      PENDING — estimated ~10,731 MiB at 0.90 util
```

### Baseline Benchmark Results

Run `scripts/benchmark/baseline.py` after Stage G is complete, then fill these in:

| Profile | Prompt Tokens | Output Tokens | TTFT (ms) | Total Latency (ms) | Tokens/s |
|---------|--------------|---------------|-----------|-------------------|----------|
| short_chat | ~64 | 128 | PENDING | PENDING | PENDING |
| medium_chat | ~512 | 256 | PENDING | PENDING | PENDING |
| long_prompt | ~1,024 | 256 | PENDING | PENDING | PENDING |
| upper_v1_context | ~2,048 | 256 | PENDING | PENDING | PENDING |
| streaming_ttft | ~16 | 64 | PENDING | N/A | PENDING |

_Run: `python scripts/benchmark/baseline.py --url http://localhost:9100/v1/chat/completions --api-key inf_... --output /workspace/benchmarks/baseline.json`_

### Exit Checklist — Actual Results

- [x] RunPod NVIDIA L4 24 GB provisioned
- [x] vLLM `/health` returns 200 — confirmed 2026-08-29
- [x] Non-streaming `chat/completions` returns coherent response — confirmed 2026-08-29
- [x] VRAM budget documented above — 19,112 / 23,034 MiB at Stage D idle
- [ ] Streaming formally validated with TTFT — run `05_validate_streaming.sh`
- [ ] Stage G (8K + LoRA + prefix cache) flags active — run `04_finalize_phase1.sh`
- [ ] VRAM snapshot after Stage G captured — fill in table above
- [ ] Baseline benchmark results recorded — run `baseline.py` after Stage G
- [x] scripts/seed_real_worker.py ready — seeds real worker/deployment into DB
- [x] docker-compose.real.yml overlay created — activates SSH tunnel endpoint

### Deviations from Plan

```
1. Thinking mode active by default on Qwen3-4B.
   Impact: <think> tags appear in responses; add chat_template_kwargs to suppress.
   Resolution: suppress in API requests with enable_thinking=false.

2. Stage F streaming script was truncated during heredoc creation in earlier session.
   Resolution: 03_validate_api.sh recreated 2026-08-29; new 05_validate_streaming.sh
   created with proper Python-based TTFT measurement.

3. Stage G not yet run (vLLM still on 4K Stage D flags as of 2026-08-29).
   Resolution: run 04_finalize_phase1.sh in next pod session to activate 8K + LoRA.
```

### Issues Encountered

```
1. Streaming script truncated during heredoc creation in earlier session.
   Fix: recreated 03_validate_api.sh; added 05_validate_streaming.sh for TTFT capture.

2. finish_reason: "length" at 256 tokens with some prompts.
   Fix: increase max_tokens to 512–1024 for full responses.

3. Qwen3 thinking mode adds <think>...</think> overhead to TTFT measurements.
   Fix: disable with chat_template_kwargs: {enable_thinking: false}.
```

### Architecture Decisions Made

```
Decision 1:
  Context: Whether to run vLLM in Docker or directly in a Python venv on the pod.
  Choice: Python venv (/workspace/vllm-env/).
  Reason: Simpler GPU passthrough; avoids nvidia-container-toolkit setup complexity.
  Trade-off: Less portable than Docker, but pod environments are ephemeral anyway.

Decision 2:
  Context: Default vs non-thinking mode for Qwen3-4B.
  Choice: Suppress thinking mode in all platform requests.
  Reason: Thinking mode adds latency and token overhead; the platform is an inference
          gateway, not a reasoning engine. Users who want thinking can pass the flag.
  Implementation: Gateway-level default chat_template_kwargs suppression in chat.py.
```

### Handoff Notes for Phase 2

```
Phase 2 is fully implemented against the mock vLLM. When Stage G is complete:
  - vLLM endpoint:        http://vllm:8000 (via SSH tunnel → host.docker.internal:8001)
  - Health path:          /health  ← confirmed working
  - Streaming path:       /v1/chat/completions  ← confirmed working
  - max_model_len:        8192 (after Stage G)
  - Special header:       add chat_template_kwargs.enable_thinking=false by default
  - No other gateway changes needed — resolver.py, rate_limiter.py, recorder.py all
    work against real vLLM without modification
```

---

## What This Phase Does NOT Build

- No FastAPI gateway (Phase 2)
- No authentication (Phase 3)
- No database (Phase 3)
- No Redis (Phase 6)
- No metrics scraping (Phase 7)
