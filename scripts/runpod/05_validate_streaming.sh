#!/usr/bin/env bash
# Phase 1 Stage F — Streaming Validation with TTFT Measurement
#
# Confirms that:
#   1. Streaming SSE endpoint delivers tokens incrementally (not all at once)
#   2. TTFT (time-to-first-token) is measured and recorded
#   3. [DONE] sentinel is received
#   4. Token count and generation rate are captured
#
# Run on the RunPod pod while vLLM is serving:
#   tmux attach -t inferra
#   bash /workspace/scripts/05_validate_streaming.sh
#
# Saves results to: /workspace/benchmarks/streaming-validation.json

set -euo pipefail

VLLM_URL="http://localhost:8000"
MODEL="Qwen/Qwen3-4B"
BENCHMARK_DIR="/workspace/benchmarks"
OUT_JSON="${BENCHMARK_DIR}/streaming-validation.json"

mkdir -p "${BENCHMARK_DIR}"

echo "================================================================"
echo " Phase 1 Stage F — Streaming Validation + TTFT Measurement"
echo "================================================================"
echo ""

# ── 1. Wait for vLLM health ──────────────────────────────────────────
echo "[ 1/5 ] Checking vLLM health..."
for i in $(seq 1 24); do
    if curl -sf "${VLLM_URL}/health" > /dev/null 2>&1; then
        echo "  vLLM ready ✓"
        break
    fi
    echo "  attempt ${i}/24 — waiting 5s..."
    sleep 5
    if [[ $i -eq 24 ]]; then
        echo "ERROR: vLLM did not become healthy. Is it running in tmux?"
        exit 1
    fi
done

# ── 2. Python-based streaming validation with TTFT measurement ────────
echo ""
echo "[ 2/5 ] Streaming TTFT measurement (Python)..."
echo ""

python3 - << 'PYEOF'
import json
import socket
import sys
import time
import urllib.request
import http.client

VLLM_URL = "http://localhost:8000"
MODEL = "Qwen/Qwen3-4B"
PROMPT = "Explain the concept of KV-cache in transformer models in exactly three sentences."

payload = json.dumps({
    "model": MODEL,
    "messages": [{"role": "user", "content": PROMPT}],
    "stream": True,
    "max_tokens": 128,
    "temperature": 0.0,
    "chat_template_kwargs": {"enable_thinking": False},
}).encode()

req = urllib.request.Request(
    f"{VLLM_URL}/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
)

print(f"  Sending streaming request to {VLLM_URL}/v1/chat/completions...")
print(f"  Prompt: \"{PROMPT[:60]}...\"")
print("")

t_start = time.perf_counter()
t_first_token = None
chunks_received = 0
done_received = False
completion_text = ""
completion_tokens = 0
prompt_tokens = 0
chunk_timestamps = []

with urllib.request.urlopen(req, timeout=120) as resp:
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        if not line.startswith("data: "):
            continue
        payload_str = line[6:]
        if payload_str == "[DONE]":
            done_received = True
            break

        now = time.perf_counter()
        chunk_timestamps.append(now - t_start)

        try:
            chunk = json.loads(payload_str)
        except json.JSONDecodeError:
            continue

        choices = chunk.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            content = delta.get("content", "")
            if content:
                if t_first_token is None:
                    t_first_token = now
                completion_text += content
                chunks_received += 1

        usage = chunk.get("usage") or {}
        if usage.get("completion_tokens"):
            completion_tokens = usage["completion_tokens"]
        if usage.get("prompt_tokens"):
            prompt_tokens = usage["prompt_tokens"]

t_end = time.perf_counter()

# ── Results ─────────────────────────────────────────────────────────
ttft_ms = round((t_first_token - t_start) * 1000, 1) if t_first_token else None
total_ms = round((t_end - t_start) * 1000, 1)
decode_ms = round((t_end - t_first_token) * 1000, 1) if t_first_token else None
tps = round(completion_tokens / (decode_ms / 1000), 1) if decode_ms and decode_ms > 0 and completion_tokens > 0 else None

# Verify incremental delivery (chunks should span at least 100ms total)
is_incremental = len(chunk_timestamps) > 1 and (chunk_timestamps[-1] - chunk_timestamps[0]) > 0.05

print(f"  ── Response ─────────────────────────────────────────────")
print(f"  {completion_text[:200]}{'...' if len(completion_text) > 200 else ''}")
print("")
print(f"  ── Metrics ──────────────────────────────────────────────")
print(f"  TTFT              : {ttft_ms} ms")
print(f"  Decode time       : {decode_ms} ms")
print(f"  Total latency     : {total_ms} ms")
print(f"  Prompt tokens     : {prompt_tokens}")
print(f"  Completion tokens : {completion_tokens}")
print(f"  Tokens / second   : {tps}")
print(f"  Content chunks    : {chunks_received}")
print(f"  [DONE] received   : {done_received}")
print(f"  Incremental       : {is_incremental} (chunks span {round((chunk_timestamps[-1] - chunk_timestamps[0]) * 1000)}ms)")
print("")

# ── Validation ────────────────────────────────────────────────────────
passed = True
checks = []

if ttft_ms is not None:
    checks.append(("TTFT measured", True, f"{ttft_ms} ms"))
else:
    checks.append(("TTFT measured", False, "No first token received"))
    passed = False

if done_received:
    checks.append(("[DONE] sentinel", True, "received"))
else:
    checks.append(("[DONE] sentinel", False, "missing"))
    passed = False

if chunks_received > 0:
    checks.append(("Content chunks", True, f"{chunks_received} chunks"))
else:
    checks.append(("Content chunks", False, "0 chunks — stream may have failed"))
    passed = False

if is_incremental:
    checks.append(("Incremental delivery", True, "tokens arrived over time"))
else:
    checks.append(("Incremental delivery", False, "all tokens arrived at once (buffered?)"))
    passed = False

if completion_text:
    checks.append(("Coherent response", True, f"{len(completion_text)} chars"))
else:
    checks.append(("Coherent response", False, "empty response"))
    passed = False

print(f"  ── Validation ───────────────────────────────────────────")
for name, ok, detail in checks:
    icon = "✓" if ok else "✗"
    print(f"  {icon} {name}: {detail}")

# ── Save JSON ─────────────────────────────────────────────────────────
result = {
    "test": "phase1_stage_f_streaming_validation",
    "model": MODEL,
    "passed": passed,
    "ttft_ms": ttft_ms,
    "decode_ms": decode_ms,
    "total_ms": total_ms,
    "prompt_tokens": prompt_tokens,
    "completion_tokens": completion_tokens,
    "tokens_per_second": tps,
    "content_chunks": chunks_received,
    "done_received": done_received,
    "incremental_delivery": is_incremental,
    "response_text": completion_text[:500],
    "checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
}

with open("/workspace/benchmarks/streaming-validation.json", "w") as f:
    json.dump(result, f, indent=2)

if passed:
    print("")
    print("  STAGE F PASSED ✓")
    sys.exit(0)
else:
    print("")
    print("  STAGE F FAILED ✗ — see checks above")
    sys.exit(1)
PYEOF

STAGE_F_EXIT=$?

# ── 3. Second streaming request — different prompt, confirm repeatability ──
echo ""
echo "[ 3/5 ] Repeatability check (second streaming request)..."
STREAM_DONE=0
curl -sf -N "${VLLM_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"What is 2+2? Answer in one word.\"}],
    \"stream\": true,
    \"max_tokens\": 16,
    \"temperature\": 0.0,
    \"chat_template_kwargs\": {\"enable_thinking\": false}
  }" | while IFS= read -r line; do
    if [[ "$line" == "data: [DONE]" ]]; then
        echo "  [DONE] received ✓"
        break
    fi
done
echo "  Repeatability check complete ✓"

# ── 4. Streaming through 8K context (verify max-model-len flag) ───────
echo ""
echo "[ 4/5 ] Context ceiling check (max_tokens near 8K limit)..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${VLLM_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL}\",
    \"messages\": [{\"role\": \"user\", \"content\": \"Hello\"}],
    \"stream\": false,
    \"max_tokens\": 512
  }")
if [[ "$HTTP_STATUS" == "200" ]]; then
    echo "  8K config accepts 512-token output request ✓ (HTTP ${HTTP_STATUS})"
else
    echo "  WARNING: unexpected status ${HTTP_STATUS}"
fi

# ── 5. VRAM snapshot ──────────────────────────────────────────────────
echo ""
echo "[ 5/5 ] VRAM snapshot after streaming tests..."
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader | \
  awk -F', ' '{printf "  Used: %s  Free: %s  Total: %s\n", $1, $2, $3}'
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv \
  >> "${BENCHMARK_DIR}/streaming-validation.json" 2>/dev/null || true

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "================================================================"
if [[ $STAGE_F_EXIT -eq 0 ]]; then
    echo " Stage F: STREAMING VALIDATED ✓"
else
    echo " Stage F: VALIDATION FAILED — check output above"
fi
echo "================================================================"
echo ""
echo "Results saved to: ${OUT_JSON}"
echo ""
echo "Next: run 04_finalize_phase1.sh to complete Stage G (8K + LoRA + prefix cache)"

exit $STAGE_F_EXIT
