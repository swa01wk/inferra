#!/usr/bin/env bash
# Stage E + F — Validate OpenAI-compatible API (non-streaming + streaming)
# Run this in a second SSH session while 02_serve_vllm.sh is running.
set -euo pipefail

VLLM_URL="http://localhost:8000"
MODEL="Qwen/Qwen3-4B"
BENCHMARK_DIR="/workspace/benchmarks"

echo "============================================"
echo " STAGE E: Validate OpenAI-Compatible API"
echo "============================================"

echo ""
echo "--- Waiting for vLLM to be ready (up to 120s) ---"
for i in $(seq 1 24); do
    if curl -sf "${VLLM_URL}/health" > /dev/null 2>&1; then
        echo "vLLM is ready!"
        break
    fi
    echo "  Attempt $i/24 — waiting 5s..."
    sleep 5
done

echo ""
echo "--- GET /v1/models ---"
curl -s "${VLLM_URL}/v1/models" | python3 -m json.tool

echo ""
echo "--- POST /v1/chat/completions (non-streaming) ---"
RESPONSE=$(curl -s "${VLLM_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [
      {\"role\": \"user\", \"content\": \"Explain KV cache in simple terms.\"}
    ],
    \"temperature\": 0.6,
    \"max_tokens\": 256
  }")

echo "$RESPONSE" | python3 -m json.tool
echo "$RESPONSE" > "${BENCHMARK_DIR}/test-non-streaming-response.json"

echo ""
echo "--- Checking for CUDA OOM in response ---"
if echo "$RESPONSE" | grep -qi "oom\|out of memory\|error"; then
    echo "WARNING: Possible OOM or error in response!"
else
    echo "OK: No OOM detected."
fi

echo ""
echo "============================================"
echo " STAGE F: Validate Streaming"
echo "============================================"

echo ""
echo "--- POST /v1/chat/completions (stream=true) ---"
echo "Tokens should appear incrementally:"
echo ""
curl -N "${VLLM_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [
      {\"role\": \"user\", \"content\": \"Explain how transformer attention works.\"}
    ],
    \"stream\": true,
    \"max_tokens\": 256
  }"

echo ""
echo ""
echo "============================================"
echo " STAGE E + F complete."
echo " --- Now capturing baseline metrics ---"
echo "============================================"

echo ""
echo "--- GPU VRAM after model load ---"
nvidia-smi
nvidia-smi > "${BENCHMARK_DIR}/gpu-after-vllm-load.txt"

echo ""
echo "--- Baseline benchmark: single request, 256-token prompt, 128-token output ---"
python3 - <<'PYEOF'
import json, time, subprocess, urllib.request

VLLM_URL = "http://localhost:8000"
MODEL = "Qwen/Qwen3-4B"
BENCHMARK_DIR = "/workspace/benchmarks"

prompt = " ".join(["word"] * 256)  # ~256 token prompt

payload = json.dumps({
    "model": MODEL,
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.0,
    "max_tokens": 128,
    "stream": False,
}).encode()

req = urllib.request.Request(
    f"{VLLM_URL}/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
)

t0 = time.perf_counter()
with urllib.request.urlopen(req, timeout=120) as resp:
    raw = resp.read()
t1 = time.perf_counter()

data = json.loads(raw)
total_ms = round((t1 - t0) * 1000, 1)
usage = data.get("usage", {})
prompt_tokens = usage.get("prompt_tokens", "?")
completion_tokens = usage.get("completion_tokens", "?")

print(f"Prompt tokens     : {prompt_tokens}")
print(f"Completion tokens : {completion_tokens}")
print(f"Total latency     : {total_ms} ms")
if isinstance(completion_tokens, int) and completion_tokens > 0:
    tps = round(completion_tokens / (t1 - t0), 1)
    print(f"Output tokens/sec : {tps}")

result = {
    "test": "baseline_single_256prompt_128output",
    "prompt_tokens": prompt_tokens,
    "completion_tokens": completion_tokens,
    "total_latency_ms": total_ms,
}
with open(f"{BENCHMARK_DIR}/baseline-metrics.json", "w") as f:
    json.dump(result, f, indent=2)
print(f"\nSaved to {BENCHMARK_DIR}/baseline-metrics.json")
PYEOF

echo ""
echo "============================================"
echo " All Stage A-F validations complete!"
echo " Check /workspace/benchmarks/ for results."
echo "============================================"
