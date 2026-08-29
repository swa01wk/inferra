#!/usr/bin/env bash
# Phase 1 close-out: restart vLLM with V1 production flags (Stage G),
# confirm streaming, capture baseline metrics.
#
# Run this ON the RunPod pod (inside the tmux session):
#   tmux attach -t inferra
#   bash /workspace/scripts/04_finalize_phase1.sh
#
# This script kills the current vLLM process and restarts it with:
#   - 8192 context (up from 4096)
#   - LoRA enabled (--enable-lora, rank 16, 4 slots)
#   - Prefix caching enabled
# It then waits for healthy, confirms streaming, and captures benchmarks.

set -euo pipefail

WORKSPACE=/workspace
MODEL_PATH="${WORKSPACE}/models/Qwen3-4B"
VENV="${WORKSPACE}/vllm-env"
BENCHMARKS="${WORKSPACE}/benchmarks"
URL="http://localhost:8000"
MODEL="Qwen/Qwen3-4B"

echo "================================================================"
echo " Inferra — Phase 1 Stage G: V1 Production vLLM Restart"
echo "================================================================"

# ── 1. Activate venv ──────────────────────────────────────────────────
source "${VENV}/bin/activate"
export HF_HOME="${WORKSPACE}/huggingface-cache"

# ── 2. Kill existing vLLM process ────────────────────────────────────
echo ""
echo "--- Stopping existing vLLM process ---"
if pkill -f "vllm serve" 2>/dev/null; then
    echo "  vLLM stopped."
    sleep 3
else
    echo "  No existing vLLM process found (OK)."
fi

# ── 3. GPU snapshot before restart ───────────────────────────────────
echo ""
echo "--- GPU state before restart ---"
nvidia-smi
mkdir -p "${BENCHMARKS}"
nvidia-smi > "${BENCHMARKS}/gpu-before-v1-restart.txt"

# ── 4. Start vLLM with V1 production flags ───────────────────────────
echo ""
echo "--- Starting vLLM with V1 production flags ---"
echo "    dtype:          bfloat16"
echo "    max-model-len:  8192"
echo "    gpu-mem-util:   0.90"
echo "    enable-lora:    true (rank 16, 4 slots)"
echo "    prefix-caching: true"
echo ""

# Start vLLM in a new tmux window so it keeps running after this script exits
tmux new-window -t inferra -n "vllm-v1" \
    "source ${VENV}/bin/activate && export HF_HOME=${WORKSPACE}/huggingface-cache && \
     vllm serve ${MODEL_PATH} \
       --served-model-name ${MODEL} \
       --dtype bfloat16 \
       --max-model-len 8192 \
       --gpu-memory-utilization 0.90 \
       --enable-lora \
       --max-lora-rank 16 \
       --max-loras 4 \
       --enable-prefix-caching \
       --host 0.0.0.0 \
       --port 8000 2>&1 | tee ${WORKSPACE}/logs/vllm-v1.log"

echo "  vLLM started in tmux window 'vllm-v1'. Waiting for health..."
echo "  (monitor: tmux select-window -t inferra:vllm-v1)"

# ── 5. Wait for healthy (up to 3 minutes) ────────────────────────────
echo ""
echo "--- Waiting for vLLM /health (up to 180s) ---"
READY=0
for i in $(seq 1 36); do
    if curl -sf "${URL}/health" > /dev/null 2>&1; then
        echo "  Ready after ~$((i * 5))s"
        READY=1
        break
    fi
    echo "  attempt ${i}/36 — waiting 5s..."
    sleep 5
done

if [[ $READY -eq 0 ]]; then
    echo "ERROR: vLLM did not become healthy within 180s."
    echo "Check logs: tmux select-window -t inferra:vllm-v1"
    exit 1
fi

# ── 6. Verify models endpoint ─────────────────────────────────────────
echo ""
echo "=== GET /v1/models ==="
curl -s "${URL}/v1/models" | python3 -m json.tool

# ── 7. Non-streaming test ─────────────────────────────────────────────
echo ""
echo "=== POST /v1/chat/completions (non-streaming, 8K ctx test) ==="
curl -s "${URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"What is vLLM and why is it fast?\"}],
    \"temperature\": 0.6,
    \"max_tokens\": 256,
    \"chat_template_kwargs\": {\"enable_thinking\": false}
  }" \
  | tee "${BENCHMARKS}/test-v1-nonstream.json" | python3 -m json.tool

# ── 8. Streaming test ────────────────────────────────────────────────
echo ""
echo "=== POST /v1/chat/completions (stream=true) ==="
curl -N "${URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Explain transformer attention in 3 sentences.\"}],
    \"stream\": true,
    \"max_tokens\": 128,
    \"chat_template_kwargs\": {\"enable_thinking\": false}
  }"
echo ""
echo "(streaming complete)"

# ── 9. Capture VRAM and GPU state after model load ────────────────────
echo ""
echo "=== GPU state after V1 load ==="
nvidia-smi | tee "${BENCHMARKS}/gpu-after-v1-load.txt"

# ── 10. VRAM budget detail ────────────────────────────────────────────
echo ""
echo "=== VRAM budget ==="
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv \
  | tee "${BENCHMARKS}/vram-v1-baseline.csv"

# ── 11. Simple latency snapshot (single request) ─────────────────────
echo ""
echo "=== Single-request latency snapshot ==="
python3 - << 'PYEOF'
import json, time, urllib.request

url = "http://localhost:8000/v1/chat/completions"
payload = {
    "model": "Qwen/Qwen3-4B",
    "messages": [{"role": "user", "content": "Explain KV cache in one sentence."}],
    "stream": False,
    "max_tokens": 128,
    "chat_template_kwargs": {"enable_thinking": False},
}
req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
t0 = time.time()
with urllib.request.urlopen(req, timeout=120) as resp:
    body = json.loads(resp.read().decode())
total_ms = int((time.time() - t0) * 1000)
usage = body.get("usage", {})
tps = usage.get("completion_tokens", 0) / (total_ms / 1000) if total_ms > 0 else 0
result = {
    "total_latency_ms": total_ms,
    "prompt_tokens": usage.get("prompt_tokens", 0),
    "completion_tokens": usage.get("completion_tokens", 0),
    "approx_tokens_per_sec": round(tps, 1),
    "finish_reason": body.get("choices", [{}])[0].get("finish_reason"),
}
print(json.dumps(result, indent=2))
with open("/workspace/benchmarks/latency-snapshot-v1.json", "w") as f:
    json.dump(result, f, indent=2)
print("\nSaved to /workspace/benchmarks/latency-snapshot-v1.json")
PYEOF

# ── 12. Summary ───────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo " Phase 1 Stage G — COMPLETE"
echo "================================================================"
echo ""
echo "Benchmark files written to ${BENCHMARKS}/:"
ls -lah "${BENCHMARKS}/"
echo ""
echo "vLLM V1 flags active:"
echo "  --max-model-len 8192"
echo "  --enable-lora --max-lora-rank 16 --max-loras 4"
echo "  --enable-prefix-caching"
echo ""
echo "Next: on your Mac, run:"
echo "  ./scripts/integrate.sh <container-id>"
echo "  (container-id from RunPod dashboard → Connect → SSH)"
