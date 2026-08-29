# RunPod L4 POC — Runbook

**Model:** Qwen/Qwen3-4B | **Runtime:** vLLM | **GPU:** NVIDIA L4 24 GB  
**Guide ref:** `Inferra_V1_RunPod_L4_Qwen3_4B_vLLM_POC_Implementation_Guide_Updated.docx`

---

## ⚠️ Critical Notes Before Starting

| Note | Detail |
|------|--------|
| **SSH hostname changes on restart** | The container portion of the hostname (e.g., `-64410f27`) changes every time the Pod is restarted. Always get the fresh SSH command from the RunPod dashboard → Pod → Connect → SSH. |
| **Pod ID stays the same** | The prefix (`5fmoz125ju1zc0`) stays fixed. Only the `-XXXXXXXX` suffix rotates. |
| **Billing while running** | GPU compute is billed while the Pod is `RUNNING`, regardless of whether SSH is connected. Stop the Pod from the dashboard when done. |
| **Persistent storage survives restarts** | `/workspace` is a persistent network volume. Model weights, venvs, scripts, and benchmarks stored here survive Pod restarts. |
| **SSH key passphrase** | The key `~/.ssh/id_ed25519_runpod` has a passphrase. Load it once per Mac session with `ssh-add`. |

---

## 0. One-Time Mac Setup

```bash
# Load SSH key into agent (enter passphrase once)
ssh-add ~/.ssh/id_ed25519_runpod

# Verify it is loaded
ssh-add -l
```

---

## 1. Get Fresh SSH Command After Each Restart

The SSH hostname changes every Pod restart. Always copy the current command from:

> RunPod Dashboard → Your Pod → **Connect** button → SSH tab

The format is always:
```
ssh -i ~/.ssh/id_ed25519_runpod <POD_ID>-<CONTAINER_ID>@ssh.runpod.io
```

Set a shell variable so you don't retype it:

```bash
# Replace the value below with the current hostname from the dashboard each session
export RUNPOD="5fmoz125ju1zc0-64410f27@ssh.runpod.io"
```

> From this point all commands use `$RUNPOD` as the host — just update that one variable each session.

---

## 2. Connect and Start tmux

```bash
ssh $RUNPOD
```

Inside the RunPod shell:

```bash
# Attach to existing session if the Pod was just restarted
tmux attach -t inferra 2>/dev/null || tmux new -s inferra
```

---

## 3. Create Scripts on the Remote (First Time Only)

Scripts live on `/workspace` (persistent), so this only needs to be done **once**. After a Pod restart, skip to [Section 6 — Resume After Restart](#6-resume-after-restart).

### 3a. `00_validate_gpu.sh`

```bash
mkdir -p /workspace/scripts && cat > /workspace/scripts/00_validate_gpu.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "=== nvidia-smi ===" && nvidia-smi
echo ""
echo "=== PyTorch / CUDA ==="
python3 -c "
import torch
print('PyTorch :', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU       :', torch.cuda.get_device_name(0))
    props = torch.cuda.get_device_properties(0)
    print('VRAM (GB) :', round(props.total_memory / 1024**3, 2))
"
echo ""
echo "=== Python version ===" && python3 --version
echo "=== Disk layout ===" && df -h
echo "=== /workspace ===" && ls -lah /workspace
EOF
chmod +x /workspace/scripts/00_validate_gpu.sh
```

### 3b. `01_setup_env.sh`

```bash
cat > /workspace/scripts/01_setup_env.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /workspace
echo "--- Creating venv at /workspace/vllm-env ---"
python3 -m venv vllm-env
source vllm-env/bin/activate
pip install --upgrade pip
pip install vllm
vllm --version
python3 -c "import vllm; print(vllm.__version__)" > /workspace/vllm-version.txt
echo "--- Creating persistent directories ---"
mkdir -p /workspace/huggingface-cache /workspace/models \
         /workspace/adapters/test /workspace/adapters/tenant-specific \
         /workspace/benchmarks /workspace/logs \
         /workspace/inference-platform/{apps,tests,scripts,docs}
export HF_HOME=/workspace/huggingface-cache
grep -q HF_HOME ~/.bashrc 2>/dev/null || echo 'export HF_HOME=/workspace/huggingface-cache' >> ~/.bashrc
echo "--- Idle GPU snapshot ---"
nvidia-smi > /workspace/benchmarks/gpu-before-vllm.txt
cat /workspace/benchmarks/gpu-before-vllm.txt
echo "--- Disk before download ---" && df -h /workspace
pip install -U "huggingface_hub[cli]"
echo "--- Downloading Qwen/Qwen3-4B (~8 GB) ---"
HF_HOME=/workspace/huggingface-cache hf download Qwen/Qwen3-4B --local-dir /workspace/models/Qwen3-4B
echo "--- Verifying model ---"
du -sh /workspace/models/Qwen3-4B && ls -lah /workspace/models/Qwen3-4B
df -h /workspace
echo "=== Stages B+C complete ==="
EOF
chmod +x /workspace/scripts/01_setup_env.sh
```

### 3c. `02_serve_vllm.sh`

```bash
cat > /workspace/scripts/02_serve_vllm.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
export HF_HOME=/workspace/huggingface-cache
source /workspace/vllm-env/bin/activate
echo "Starting vLLM on port 8000 (BF16 | 4096 ctx | 0.85 gpu-mem)..."
vllm serve /workspace/models/Qwen3-4B \
    --served-model-name Qwen/Qwen3-4B \
    --dtype bfloat16 \
    --max-model-len 4096 \
    --gpu-memory-utilization 0.85 \
    --port 8000
EOF
chmod +x /workspace/scripts/02_serve_vllm.sh
```

### 3d. `03_validate_api.sh`

```bash
cat > /workspace/scripts/03_validate_api.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
URL="http://localhost:8000"
MODEL="Qwen/Qwen3-4B"
mkdir -p /workspace/benchmarks
echo "--- Waiting for vLLM (up to 120s) ---"
for i in $(seq 1 24); do
  curl -sf "${URL}/health" > /dev/null 2>&1 && echo "Ready!" && break
  echo "  attempt $i/24 — waiting 5s..."; sleep 5
done
echo ""
echo "=== GET /v1/models ==="
curl -s "${URL}/v1/models" | python3 -m json.tool
echo ""
echo "=== POST /v1/chat/completions (non-streaming) ==="
curl -s "${URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain KV cache in simple terms.\"}],\"temperature\":0.6,\"max_tokens\":256}" \
  | tee /workspace/benchmarks/test-non-streaming.json | python3 -m json.tool
echo ""
echo "=== POST /v1/chat/completions (stream=true) ==="
curl -N "${URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Explain transformer attention.\"}],\"stream\":true,\"max_tokens\":256}"
echo ""
echo "=== GPU VRAM after model load ==="
nvidia-smi | tee /workspace/benchmarks/gpu-after-vllm-load.txt
echo "=== All checks done. Results in /workspace/benchmarks/ ==="
EOF
chmod +x /workspace/scripts/03_validate_api.sh
```

---

## 4. First-Time Full Setup Sequence

Run these **in order**, all inside tmux window 1:

```bash
# Stage A — Validate GPU
bash /workspace/scripts/00_validate_gpu.sh

# Stage B+C — Install vLLM + download Qwen3-4B (~10-15 min)
bash /workspace/scripts/01_setup_env.sh
```

Open tmux window 2 (`Ctrl+B, c`) and start vLLM:

```bash
# Stage D — Start inference server
bash /workspace/scripts/02_serve_vllm.sh
```

> Wait for: `INFO: Uvicorn running on http://0.0.0.0:8000`

Open tmux window 3 (`Ctrl+B, c`) and validate:

```bash
# Stage E+F — Validate API + streaming + capture baseline metrics
bash /workspace/scripts/03_validate_api.sh
```

---

## 5. tmux Cheat Sheet

| Keys | Action |
|------|--------|
| `Ctrl+B, c` | New window |
| `Ctrl+B, 0` / `1` / `2` | Switch to window 0 / 1 / 2 |
| `Ctrl+B, d` | Detach (everything keeps running) |
| `tmux attach -t inferra` | Reattach after reconnect |
| `Ctrl+B, [` | Scroll mode (use arrow keys, `q` to exit) |

---

## 6. Resume After Restart

When the Pod is restarted the container hostname changes. Do this:

```bash
# 1. Get new SSH command from RunPod dashboard → Connect → SSH
# 2. Update RUNPOD variable on your Mac
export RUNPOD="5fmoz125ju1zc0-<NEW_CONTAINER_ID>@ssh.runpod.io"

# 3. Connect
ssh $RUNPOD

# 4. Reattach tmux (or start fresh)
tmux attach -t inferra 2>/dev/null || tmux new -s inferra

# 5. Scripts are still on /workspace (persistent) — no re-upload needed
ls /workspace/scripts/

# 6. Re-validate GPU (always good after restart)
bash /workspace/scripts/00_validate_gpu.sh

# 7. Model is still downloaded — just start vLLM directly
bash /workspace/scripts/02_serve_vllm.sh

# 8. In a new tmux window, validate API
bash /workspace/scripts/03_validate_api.sh
```

> **The vLLM venv and Qwen3-4B model are on `/workspace` (persistent), so you skip Stages B+C entirely after the first setup.**

---

## 7. Verifying GPU Is Actually Being Used

The RunPod dashboard shows two separate metrics that are often confused:

| Metric | What it means | Expected at idle |
|--------|---------------|-----------------|
| **VRAM %** (memory ring) | Model weights loaded in GPU memory | ~83% (19 GB / 23 GB) — stays high while vLLM is running |
| **GPU Util %** (compute ring) | GPU cores actively computing | **0%** between requests — spikes to ~100% during inference |

VRAM 83% + GPU 0% = correct healthy idle state. The model is loaded and ready; it just isn't generating tokens right now.

### Method 1 — Watch GPU live during a request (most definitive)

Open two tmux windows (`Ctrl+B, c` to create, `Ctrl+B, 0/1` to switch).

**Window 1 — live GPU monitor:**
```bash
watch -n 1 nvidia-smi
```

**Window 2 — fire a request:**
```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-4B","messages":[{"role":"user","content":"Count from 1 to 100 slowly."}],"max_tokens":512}' \
  | python3 -m json.tool
```

You will see GPU-Util jump from **0% → ~100%** while the model generates, then drop back to 0% when done.

### Method 2 — Quick one-shot snapshot

```bash
nvidia-smi
```

Look for:
```
|   0  NVIDIA L4   ...  19112MiB / 23034MiB |   0%   Default |
```
- `19112MiB` used → model is loaded in VRAM ✅
- `0%` → idle, no active request — normal ✅

### Method 3 — Confirm the vLLM process owns the VRAM

```bash
nvidia-smi --query-compute-apps=pid,used_memory,process_name --format=csv
```

Expected output:
```
pid, used_gpu_memory [MiB], process_name
2520, 19104 MiB, VLLM::EngineCore
```

### Method 4 — PyTorch VRAM check from inside the venv

```bash
source /workspace/vllm-env/bin/activate
python3 -c "
import torch
print('CUDA available:', torch.cuda.is_available())
print('GPU:', torch.cuda.get_device_name(0))
print('VRAM used (GB):', round(torch.cuda.memory_allocated(0)/1024**3, 2))
print('VRAM reserved (GB):', round(torch.cuda.memory_reserved(0)/1024**3, 2))
"
```

---

## 8. Manual Quick-Start Commands (Without Scripts)

If `/workspace/scripts/` somehow gets wiped, run these directly:

### GPU check
```bash
nvidia-smi
python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### Start vLLM
```bash
export HF_HOME=/workspace/huggingface-cache
source /workspace/vllm-env/bin/activate
vllm serve /workspace/models/Qwen3-4B \
    --served-model-name Qwen/Qwen3-4B \
    --dtype bfloat16 \
    --max-model-len 4096 \
    --gpu-memory-utilization 0.85 \
    --port 8000
```

### Test non-streaming
```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-4B","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}' \
  | python3 -m json.tool
```

### Test streaming
```bash
curl -N http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-4B","messages":[{"role":"user","content":"Hello!"}],"stream":true,"max_tokens":64}'
```

---

## 9. Storage Layout (Persistent `/workspace`)

```
/workspace/
├── scripts/              ← all runbook scripts (this doc's scripts)
├── vllm-env/             ← Python venv with vLLM installed
├── vllm-version.txt      ← pinned vLLM version
├── huggingface-cache/    ← HF_HOME (tokenizer metadata, etc.)
├── models/
│   └── Qwen3-4B/         ← model weights (~8 GB .safetensors)
├── adapters/
│   ├── test/
│   └── tenant-specific/
├── benchmarks/           ← GPU snapshots, latency/token metrics
│   ├── gpu-before-vllm.txt
│   ├── gpu-after-vllm-load.txt
│   ├── test-non-streaming.json
│   └── baseline-metrics.json
├── logs/
└── inference-platform/   ← source code (FastAPI gateway, etc.)
    ├── apps/
    ├── tests/
    ├── scripts/
    └── docs/
```

---

## 10. Cost Reminder

| Pod State | Billing |
|-----------|---------|
| RUNNING (even idle) | ~$0.50/hr GPU compute |
| STOPPED | No GPU compute charge |
| STOPPED with volume | Small storage charge only |

**Always stop the Pod from the RunPod dashboard when done for the day.**

---

## 11. Next Milestones After POC Baseline

| Milestone | Description |
|-----------|-------------|
| M1 ✅ | Qwen3-4B under vLLM on L4 — stable chat + streaming |
| M2 | FastAPI gateway proxying vLLM — client no longer hits vLLM directly |
| M3 | Organizations + API key authentication |
| M4 | Request/token/latency persistence (every request creates a usage record) |
| M5 | Logical model aliases → deployment/adapter resolution |
| M6 | LoRA adapter registry + routing |
| M7 | RPM/concurrency/token quota controls |
| M8 | Prometheus/Grafana/OpenTelemetry dashboard |
| M9 | 4K/8K load test — safe operating envelope documented |
