# RunPod GPU Deployment

This document covers connecting the Inferra control plane to a real vLLM instance running on a RunPod NVIDIA L4 GPU pod.

---

## Overview

In the GPU deployment mode, the **data plane** (vLLM) runs on RunPod and the **control plane** (gateway, Postgres, Redis, MinIO, Prometheus, Grafana) runs locally on your Mac, connected via an SSH tunnel.

```
Mac
├── SSH tunnel: localhost:8001 → RunPod:8000
├── docker compose (real overlay)
│   ├── api-gateway :9100  ← VLLM_BASE_URL=http://host.docker.internal:8001
│   ├── postgres / redis / minio / prometheus / grafana

RunPod Pod (inferra-v1-migration · NVIDIA L4 24 GB)
└── vLLM :8000  ← Qwen3-4B BF16
```

---

## Pod Reference

| Field | Value |
|-------|-------|
| Pod name | `inferra-v1-migration` |
| Pod ID | `5fmoz125ju1zc0` |
| GPU | NVIDIA L4 24 GB |
| VRAM at idle | ~19,112 MiB / 23,034 MiB (83%) |
| vLLM model | Qwen/Qwen3-4B · BF16 |
| Cost | $0.50/hr (GPU) while RUNNING — $0.01/hr storage when stopped |
| SSH | `ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io` |

> **Container ID changes on every restart.** Get the current one from: RunPod dashboard → Pod → Connect → SSH.

---

## Prerequisites

1. RunPod account with the `inferra-v1-migration` pod created
2. SSH key at `~/.ssh/id_ed25519_runpod` (added to RunPod account)
3. Docker Desktop running on Mac
4. Local stack successfully started at least once (see [Local Development](local-development.md))

---

## Phase 1: Set Up vLLM on the Pod

These steps are performed **on the RunPod pod** over SSH. They only need to be done once (artifacts persist in `/workspace`).

### SSH into the pod

```bash
ssh-add ~/.ssh/id_ed25519_runpod

# Get container-id from RunPod dashboard → Pod → Connect → SSH
ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io
```

### Attach to the tmux session

Always work inside tmux so processes survive SSH disconnects:

```bash
tmux attach -t inferra || tmux new-session -s inferra
```

### Stage A: Validate GPU

```bash
nvidia-smi
# Expected: NVIDIA L4, ~24 GB VRAM

python -c "
import torch
print('PyTorch:', torch.__version__)
print('CUDA:', torch.cuda.is_available())
print('GPU:', torch.cuda.get_device_name(0))
print('VRAM GB:', torch.cuda.get_device_properties(0).total_memory / 1024**3)
"
```

### Stage B: Create vLLM Environment (first time only)

```bash
cd /workspace

# Check if venv already exists
ls /workspace/vllm-env/bin/activate && echo "venv exists" || {
    python -m venv vllm-env
    source vllm-env/bin/activate
    pip install --upgrade pip
    pip install vllm==0.28.0
}
```

### Stage C: Download Qwen3-4B (first time only)

```bash
# Check if model already exists
ls /workspace/models/Qwen3-4B/*.safetensors 2>/dev/null || {
    mkdir -p /workspace/huggingface-cache
    export HF_HOME=/workspace/huggingface-cache

    source /workspace/vllm-env/bin/activate
    pip install -U "huggingface_hub[cli]"

    huggingface-cli download Qwen/Qwen3-4B \
        --local-dir /workspace/models/Qwen3-4B

    du -sh /workspace/models/Qwen3-4B
}
```

Expected size: ~8 GB.

### Stage D–E: Start vLLM (4K baseline first)

```bash
source /workspace/vllm-env/bin/activate
export HF_HOME=/workspace/huggingface-cache

# Baseline config (4K context, no LoRA)
vllm serve /workspace/models/Qwen3-4B \
  --served-model-name Qwen/Qwen3-4B \
  --dtype bfloat16 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --host 0.0.0.0 \
  --port 8000
```

Monitor VRAM in another tmux pane:
```bash
watch -n2 nvidia-smi
```

Validate from inside the pod:
```bash
curl http://localhost:8000/health
curl http://localhost:8000/v1/models
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-4B","messages":[{"role":"user","content":"Hello"}],"max_tokens":64}'
```

### Stage G: Production vLLM Configuration (8K + LoRA + prefix cache)

Once 4K baseline is validated, restart with full production flags:

```bash
# Kill current vLLM (Ctrl+C in tmux pane, or pkill -f vllm)
pkill -f "vllm serve"
sleep 3

source /workspace/vllm-env/bin/activate
export HF_HOME=/workspace/huggingface-cache

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

Wait for vLLM to print `Application startup complete`.

### Stage F: Formally Validate Streaming (with TTFT measurement)

Upload and run the dedicated streaming validation script that measures TTFT and confirms incremental delivery:

```bash
# Upload to pod (run on Mac)
scp scripts/runpod/05_validate_streaming.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On the pod
bash /workspace/scripts/05_validate_streaming.sh
```

This script:
1. Waits for vLLM to be healthy
2. Sends a streaming request and measures **TTFT** (time-to-first-token) in Python
3. Confirms tokens arrive incrementally (not buffered all at once)
4. Validates `[DONE]` sentinel is received
5. Saves `streaming-validation.json` to `/workspace/benchmarks/`

Expected output:
```
  TTFT              : 312.4 ms
  Decode time       : 1843 ms
  Completion tokens : 96
  Tokens / second   : 52.1
  Incremental       : True
  STAGE F PASSED ✓
```

**Quick manual check** (alternative if pod side upload isn't set up):
```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-4B","messages":[{"role":"user","content":"Count from 1 to 5."}],"stream":true,"max_tokens":32}'
```

Tokens should arrive incrementally (not all at once).

> **Qwen3 thinking mode:** Add `"chat_template_kwargs": {"enable_thinking": false}` to suppress `<think>` tags in responses.

---

## Phase 2: Integrate Control Plane (One-Command)

With vLLM running on the pod and the SSH key loaded, run from your Mac:

```bash
# Get the current container ID from the RunPod dashboard → Connect → SSH
# Example SSH hostname: 5fmoz125ju1zc0-64410f27@ssh.runpod.io
# Container ID: 64410f27

./scripts/integrate.sh 64410f27
```

This script automates everything:

| Step | What it does |
|------|-------------|
| 1. SSH tunnel | Opens `Mac:8001 → RunPod:8000` and verifies connectivity |
| 2. Stack teardown | Removes old Docker volumes for a clean start |
| 3. Stack start | `docker compose -f docker-compose.yml -f docker-compose.real.yml up -d` |
| 4. Dev data seed | Creates org, API keys, model record, public alias |
| 5. Real worker seed | Retires mock worker, creates real RunPod worker + deployment |
| 6. Integration tests | `pytest tests/integration -v` through real vLLM |
| 7. Baseline benchmark | Single-request latency capture through gateway |
| 8. Summary | Prints gateway URL, Grafana URL, API keys, quick test command |

### Output

```
╔══════════════════════════════════════════════════════════════╗
║  Inferra — Full Integration (Real vLLM + Control Plane)     ║
╚══════════════════════════════════════════════════════════════╝

  Gateway      : http://localhost:9100
  Grafana      : http://localhost:3000  (admin / admin)
  Inference key: inf_aBcDeFg...
  Admin key    : inf_zYxWvUt...
```

---

## Manual Integration Steps

If the automated script fails at any step, run manually:

### 1. Open SSH Tunnel

```bash
ssh-add ~/.ssh/id_ed25519_runpod

ssh -i ~/.ssh/id_ed25519_runpod \
    -L 8001:localhost:8000 \
    -N -f \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=30 \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io

# Verify
curl http://localhost:8001/health
```

### 2. Start Stack with Real Overlay

```bash
docker compose -f docker-compose.yml -f docker-compose.real.yml down -v
docker compose -f docker-compose.yml -f docker-compose.real.yml up -d --build --wait
```

### 3. Seed Data

```bash
docker compose exec api-gateway python scripts/seed_dev_data.py
# → copy INFERENCE_KEY and ADMIN_KEY
```

### 4. Seed Real Worker

```bash
docker compose exec api-gateway \
    env REAL_VLLM_ENDPOINT="http://host.docker.internal:8001" \
    python scripts/seed_real_worker.py
```

### 5. Test

```bash
export INFERRA_INFERENCE_KEY=inf_...
curl http://localhost:9100/health
curl -N http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [{"role": "user", "content": "Explain KV-cache."}],
    "stream": true,
    "max_tokens": 256
  }'
```

---

## Phase 3: Run the Full Benchmark Suite (Phase 8)

After Stage G is complete and the full stack is integrated, run all 7 benchmark stages with a single command:

```bash
# On Mac — requires stack up + inference key
export BENCHMARK_URL=http://localhost:9100/v1/chat/completions
export API_KEY=inf_<your-inference-key>
export MODEL=test-assistant

bash scripts/runpod/06_run_all_benchmarks.sh
```

Or directly against vLLM on the pod (no gateway):
```bash
# On the pod
bash /workspace/scripts/06_run_all_benchmarks.sh
```

This runs:
| Stage | Script | What it measures |
|-------|--------|-----------------|
| 1 | `baseline.py` | Single-request TTFT, total latency, tokens/s (4 prompt profiles) |
| 2 | `concurrency.py` | TTFT/latency at c=1,2,4,8,16 — stops at >2% error |
| 3 | `context_sweep.py` | 2K/4K/8K context at c=1,4,8 — KV pressure |
| 4 | `lora_mix.py` | Base vs LoRA latency at c=4,8 (requires registered adapter) |
| 5 | `prefix_cache.py` | Cold vs warm TTFT with 2K shared system prompt |
| 6 | FP8 experiment | Manual — see `plans/phase-8-benchmarking-and-beta.md §8.5 Stage 6` |
| 7 | `overload.py` | 2× RPM burst + 3× concurrent burst; 429/503 verification |

Output: all JSON files in `/workspace/benchmarks/` + auto-generated `docs/architecture/v1-capacity-report.md`.

---

## Cost Management

> **Important:** The L4 pod costs $0.50/hr while running. Stop it when not in use.

### Stop the pod (saves ~$0.49/hr)

```bash
# From the RunPod dashboard:
RunPod → Pods → inferra-v1-migration → Stop
```

When stopped, only storage costs apply (~$0.01/hr for `/workspace` volume).

### Start the pod again

```bash
RunPod → Pods → inferra-v1-migration → Start
```

After restart, get the new container ID and re-run `integrate.sh`.

### Resume vLLM after pod restart

The pod filesystem (`/workspace`) is persistent. After SSH:

```bash
tmux new-session -s inferra
source /workspace/vllm-env/bin/activate
export HF_HOME=/workspace/huggingface-cache

vllm serve /workspace/models/Qwen3-4B \
  --served-model-name Qwen/Qwen3-4B \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --enable-lora --max-lora-rank 16 --max-loras 4 \
  --enable-prefix-caching \
  --host 0.0.0.0 --port 8000
```

---

## VRAM Budget Reference

| Component | VRAM |
|-----------|------|
| Qwen3-4B BF16 weights | ~8–9 GB |
| CUDA / vLLM runtime overhead | ~2 GB |
| KV-cache pool (remaining) | ~12–13 GB |
| Total at idle after load | ~19 GB / 23 GB (83%) |

KV-cache per token (BF16):
```
36 layers × 8 KV heads × 128 head_dim × 2 (K+V) × 2 bytes = 144 KiB/token
```

| Context | KV per sequence |
|---------|----------------|
| 4,096 tokens | ~576 MiB |
| 8,192 tokens | ~1.125 GiB |

With ~12 GB KV-cache pool: safe concurrent capacity is ~10–20 sequences at 4K context.

---

## RunPod Pod Workspace Layout

```
/workspace/
├── scripts/           ← runbook scripts (recreate if missing)
├── vllm-env/          ← vLLM 0.28.0 Python venv (persistent)
├── vllm-version.txt   ← installed vLLM version
├── huggingface-cache/ ← HF model cache
├── models/
│   └── Qwen3-4B/      ← ~8 GB model weights (persistent)
├── adapters/          ← LoRA adapter cache
├── benchmarks/        ← GPU snapshots, benchmark results
├── logs/
└── inference-platform/ ← (optional) git checkout
```

All data in `/workspace` persists across pod restarts. Only GPU compute billing stops when the pod is stopped.

---

## Troubleshooting

### `ERROR: Could not reach vLLM through tunnel`

1. Check pod is in RUNNING state (not stopped)
2. Verify vLLM is serving: `ssh ... "curl http://localhost:8000/health"`
3. Verify container ID is current (changes on restart)
4. Check SSH key is loaded: `ssh-add -l | grep runpod`

### vLLM shows OOM (CUDA out of memory)

Reduce `--gpu-memory-utilization`:
```bash
# Try 0.85 instead of 0.90
vllm serve ... --gpu-memory-utilization 0.85
```

### vLLM hangs during startup

Model download may be incomplete. Check:
```bash
ls -lah /workspace/models/Qwen3-4B/*.safetensors
du -sh /workspace/models/Qwen3-4B
# Expected: ~8 GB total
```

If incomplete, re-run the download from Stage C.

### `Integration tests failed`

Check which tests failed — most require a healthy streaming connection:
```bash
pytest tests/integration/test_inference.py -v  # most critical
pytest tests/integration/test_auth.py -v
pytest tests/integration/test_rate_limits.py -v
```

### SSH tunnel drops

The tunnel keepalive (`ServerAliveInterval=30`) should prevent this. If it drops, kill and restart:
```bash
lsof -ti tcp:8001 | xargs kill -9
# Re-open tunnel (step 1 in manual integration)
```
