# First L4 Deployment Runbook

> **When to use this:** The very first time you provision the RunPod pod and download Qwen3-4B.
> After the first deployment, use [`how-to-run-all-phases.md`](how-to-run-all-phases.md)
> for day-to-day operations and resuming sessions.
>
> **Status:** As of 2026-08-29, this runbook has been executed through Stage E.
> Stage G (V1 production flags) is pending — continue at [Step 5](#step-5-stage-g-restart-with-v1-production-flags).

---

## Pod Reference

| Field | Value |
|-------|-------|
| Pod name | `inferra-v1-migration` |
| Pod ID | `5fmoz125ju1zc0` |
| GPU | NVIDIA L4 24 GB |
| Total VRAM | 23,034 MiB |
| Cost | $0.50/hr (GPU) — $0.01/hr storage (stopped) |
| SSH key | `~/.ssh/id_ed25519_runpod` |
| SSH command | `ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io` |

> **Container ID changes on every restart.** Always read the current ID from RunPod dashboard → Pod → Connect → SSH.

---

## Prerequisites

- [ ] RunPod account with billing configured
- [ ] SSH key generated and added to RunPod account (Settings → SSH Public Keys)
- [ ] SSH key at `~/.ssh/id_ed25519_runpod` on your Mac
- [ ] Docker Desktop running (for local control plane)
- [ ] HuggingFace account with access to `Qwen/Qwen3-4B` (public, no gating required)

---

## Step 1: Provision the Pod (One Time)

1. Log into [RunPod Console](https://www.runpod.io/console/pods)
2. Click **+ GPU Pod**
3. Select template: **RunPod Pytorch 2.x** (or any CUDA 12.x PyTorch base)
4. Select GPU: **NVIDIA L4** (24 GB VRAM) — cheapest L4 option
5. Set persistent storage volume at `/workspace` — at least **50 GB**
6. Name the pod: `inferra-v1-migration`
7. Click **Deploy**

Wait for pod to reach **RUNNING** state (~2 minutes).

---

## Step 2: SSH In and Set Up tmux

Always work inside tmux so processes survive SSH disconnects:

```bash
# Load SSH key (once per Mac terminal session)
ssh-add ~/.ssh/id_ed25519_runpod

# Get container ID from RunPod dashboard → Pod → Connect → SSH
# Example: 5fmoz125ju1zc0-64410f27@ssh.runpod.io → container ID is 64410f27
ssh -i ~/.ssh/id_ed25519_runpod 5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io

# Start a tmux session on the pod
tmux new-session -s inferra
```

tmux key shortcuts:
| Key | Action |
|-----|--------|
| `Ctrl+B D` | Detach (leave session running) |
| `Ctrl+B C` | New window |
| `Ctrl+B N` | Next window |
| `Ctrl+B "` | Split pane horizontally |

---

## Step 3: Stage A — Validate GPU (Confirmed)

> ✅ Already done for `inferra-v1-migration`. Skip if `/workspace/vllm-env/` exists.

```bash
# Validate GPU
nvidia-smi
# Expected: NVIDIA L4, ~24 GB VRAM

python3 -c "
import torch
print('PyTorch:', torch.__version__)
print('CUDA:', torch.cuda.is_available())
print('GPU:', torch.cuda.get_device_name(0))
print('VRAM GB:', round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1))
"
# Expected: CUDA: True, ~22.5 GB
```

---

## Step 4: Stage B+C — Create vLLM Environment and Download Model (Confirmed)

> ✅ Already done for `inferra-v1-migration`. `/workspace/vllm-env/` and `/workspace/models/Qwen3-4B/` exist.
> Skip directly to Step 5 (Stage G restart).

```bash
# Check if already set up
ls /workspace/vllm-env/bin/activate && echo "venv exists" || {
    cd /workspace
    python3 -m venv vllm-env
    source vllm-env/bin/activate
    pip install --upgrade pip
    pip install vllm==0.28.0
    vllm --version > /workspace/vllm-version.txt
    echo "vLLM installed:"
    cat /workspace/vllm-version.txt
}

# Check if model exists
ls /workspace/models/Qwen3-4B/*.safetensors 2>/dev/null | head -3 && echo "Model present" || {
    mkdir -p /workspace/huggingface-cache
    export HF_HOME=/workspace/huggingface-cache
    source /workspace/vllm-env/bin/activate
    huggingface-cli download Qwen/Qwen3-4B --local-dir /workspace/models/Qwen3-4B
    du -sh /workspace/models/Qwen3-4B
    # Expected: ~8 GB
}
```

---

## Step 5: Stage D–E — Start vLLM (4K Baseline) and Validate API

> ✅ Already done for `inferra-v1-migration`. Proceed to Stage F/G (below) on next session.

```bash
source /workspace/vllm-env/bin/activate
export HF_HOME=/workspace/huggingface-cache

# Record VRAM before model load
nvidia-smi > /workspace/benchmarks/gpu-before-vllm.txt

# Start vLLM (Stage D — 4K baseline)
vllm serve /workspace/models/Qwen3-4B \
  --served-model-name Qwen/Qwen3-4B \
  --dtype bfloat16 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --host 0.0.0.0 --port 8000
```

In a new tmux pane (`Ctrl+B "`):
```bash
# Watch VRAM during load
watch -n 2 nvidia-smi

# Validate non-streaming API
curl http://localhost:8000/health
curl http://localhost:8000/v1/models
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-4B",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "max_tokens": 64,
    "chat_template_kwargs": {"enable_thinking": false}
  }'
```

Expected VRAM at idle: **~19,112 MiB / 23,034 MiB (83%)**.

---

## Step 6: Stage F — Validate Streaming

Upload and run the dedicated TTFT measurement script:

```bash
# On Mac — upload script to pod
scp scripts/runpod/05_validate_streaming.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On pod — run it
bash /workspace/scripts/05_validate_streaming.sh
```

Expected: `STAGE F PASSED ✓` with TTFT < 1s and `streaming_ok: true` in the JSON output.

---

## Step 7: Stage G — V1 Production Flags (8K + LoRA + Prefix Cache)

Run the finalize script to restart vLLM with all V1 production flags:

```bash
# Upload to pod (on Mac)
scp scripts/runpod/04_finalize_phase1.sh \
    5fmoz125ju1zc0-<CONTAINER_ID>@ssh.runpod.io:/workspace/scripts/

# On pod — inside tmux session
tmux attach -t inferra
bash /workspace/scripts/04_finalize_phase1.sh
```

What it does:
1. Kills current vLLM process
2. Restarts in new tmux window with: `--max-model-len 8192 --gpu-memory-utilization 0.90 --enable-lora --max-lora-rank 16 --max-loras 4 --enable-prefix-caching`
3. Waits for `/health` (up to 3 minutes)
4. Runs non-streaming and streaming tests
5. Captures VRAM → `/workspace/benchmarks/gpu-after-v1-load.txt`
6. Captures latency snapshot → `/workspace/benchmarks/latency-snapshot-v1.json`

Exit criteria:
```
✅ vLLM /health returns 200 (with 8K flags)
✅ Streaming curl returns incremental tokens
✅ VRAM snapshot saved — fill into plans/phase-1-infrastructure-and-runtime.md
```

---

## Step 8: Integrate Control Plane (Mac)

With vLLM running on the pod, run from Mac:

```bash
cd /Users/swa/Desktop/inferra
./scripts/integrate.sh <CONTAINER_ID>
```

This opens the SSH tunnel, starts the full Docker Compose stack, seeds data, runs integration tests, and prints the gateway URL and API keys.

See [how-to-run-all-phases.md](how-to-run-all-phases.md) for the detailed step breakdown.

---

## Step 9: Stop the Pod When Done

> **Critical:** Pod costs $0.50/hr while running. Stop when done.

```
RunPod dashboard → inferra-v1-migration → Stop
```

All data in `/workspace` is preserved. When you start the pod again, get the new container ID and run:

```bash
tmux attach -t inferra   # if vLLM is still in the tmux window from before
# OR restart:
bash /workspace/scripts/04_finalize_phase1.sh
```

---

## Workspace Layout (after full setup)

```
/workspace/
├── scripts/
│   ├── 03_validate_api.sh         ← Stage E validation
│   ├── 04_finalize_phase1.sh      ← Stage G restart + metrics capture
│   ├── 05_validate_streaming.sh   ← Stage F TTFT measurement
│   └── 06_run_all_benchmarks.sh   ← Phase 8 benchmark suite
├── vllm-env/                      ← vLLM 0.28.0 Python venv (persistent)
├── vllm-version.txt               ← installed vLLM version
├── huggingface-cache/             ← HF model cache
├── models/
│   └── Qwen3-4B/                  ← ~8 GB Qwen3-4B weights
├── adapters/                      ← LoRA adapter cache
├── benchmarks/                    ← GPU snapshots, benchmark JSON results
│   ├── gpu-before-vllm.txt
│   ├── gpu-after-v1-load.txt
│   ├── streaming-validation.json
│   ├── baseline.json
│   └── ...
└── logs/
    └── vllm-v1.log
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| SSH connection refused | Pod not running — start from RunPod dashboard |
| `tmux: no server running` | Start new session: `tmux new-session -s inferra` |
| vLLM OOM on Stage G | Lower `--gpu-memory-utilization` to 0.87 in `04_finalize_phase1.sh` |
| `/health` times out | vLLM still loading — wait up to 3 minutes; check `tail -f /workspace/logs/vllm-v1.log` |
| `huggingface-cli: command not found` | Run `pip install -U "huggingface_hub[cli]"` inside venv |
| Model download stalls | Check disk space: `df -h /workspace`; need ~10 GB free |
| GPU Util 0% always | vLLM may be running on CPU fallback — check `nvidia-smi` during a request |
