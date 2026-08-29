#!/usr/bin/env bash
# Phase 8 — Full Benchmark Suite Orchestrator
#
# Runs all 7 benchmark stages in sequence against vLLM (direct, port 8000)
# or against the gateway (via SSH tunnel, port 8001 on Mac → 9100 through gateway).
#
# Prerequisites:
#   - vLLM is running with V1 production flags (Stage G of Phase 1)
#   - Python venv is active: source /workspace/vllm-env/bin/activate
#   - httpx is installed: pip install httpx
#
# Usage (on pod, direct to vLLM):
#   bash /workspace/scripts/06_run_all_benchmarks.sh
#
# Usage (on Mac, through gateway — requires stack running + inference key):
#   BENCHMARK_URL=http://localhost:9100/v1/chat/completions \
#   API_KEY=inf_... \
#   bash scripts/runpod/06_run_all_benchmarks.sh
#
# Outputs all results to: /workspace/benchmarks/  (or ./benchmarks/ on Mac)
# Generates final capacity report: docs/architecture/v1-capacity-report.md

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────
BENCHMARK_URL="${BENCHMARK_URL:-http://localhost:8000/v1/chat/completions}"
API_KEY="${API_KEY:-}"
MODEL="${MODEL:-Qwen/Qwen3-4B}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BENCHMARK_DIR="${BENCHMARK_DIR:-/workspace/benchmarks}"
REPORT_PATH="${PROJECT_ROOT}/docs/architecture/v1-capacity-report.md"

mkdir -p "${BENCHMARK_DIR}"

API_KEY_ARG=""
if [[ -n "$API_KEY" ]]; then
    API_KEY_ARG="--api-key ${API_KEY}"
fi

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Inferra — Phase 8 Full Benchmark Suite                       ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Target URL : ${BENCHMARK_URL}"
echo "  Model      : ${MODEL}"
echo "  Output dir : ${BENCHMARK_DIR}"
echo ""

# ── Dependency check ──────────────────────────────────────────────────
python3 -c "import httpx" 2>/dev/null || {
    echo "Installing httpx..."
    pip install httpx --quiet
}

# ── Stage 1: Single-request baseline ─────────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "Stage 1: Single-request baseline"
echo "────────────────────────────────────────────────────────────────"
python3 "${PROJECT_ROOT}/scripts/benchmark/baseline.py" \
    --url "${BENCHMARK_URL}" \
    --model "${MODEL}" \
    ${API_KEY_ARG} \
    --output "${BENCHMARK_DIR}/baseline.json"
echo ""

# ── Stage 2: Concurrency sweep ────────────────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "Stage 2: Concurrency sweep (1 → 2 → 4 → 8 → 16)"
echo "         Stop criterion: error rate > 2% OR P99 TTFT > 10s"
echo "────────────────────────────────────────────────────────────────"
python3 "${PROJECT_ROOT}/scripts/benchmark/concurrency.py" \
    --url "${BENCHMARK_URL}" \
    --model "${MODEL}" \
    ${API_KEY_ARG} \
    --concurrency 1 2 4 8 16 \
    --requests-per-level 20 \
    --max-tokens 256 \
    --stream \
    --output "${BENCHMARK_DIR}/concurrency.json"
echo ""

# ── Stage 3: Context sweep ────────────────────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "Stage 3: Context sweep (2K / 4K / 8K at concurrency 1, 4, 8)"
echo "────────────────────────────────────────────────────────────────"
python3 "${PROJECT_ROOT}/scripts/benchmark/context_sweep.py" \
    --url "${BENCHMARK_URL}" \
    --model "${MODEL}" \
    ${API_KEY_ARG} \
    --context-sizes 2048 4096 8192 \
    --concurrency-levels 1 4 8 \
    --max-output-tokens 256 \
    --repeats 5 \
    --output "${BENCHMARK_DIR}/context_sweep.json"
echo ""

# ── Stage 4: LoRA mix ─────────────────────────────────────────────────
LORA_ALIAS="${LORA_ALIAS:-}"
if [[ -n "$LORA_ALIAS" ]]; then
    echo "────────────────────────────────────────────────────────────────"
    echo "Stage 4: LoRA mix (base vs adapter at concurrency 4 and 8)"
    echo "────────────────────────────────────────────────────────────────"
    python3 "${PROJECT_ROOT}/scripts/benchmark/lora_mix.py" \
        --url "${BENCHMARK_URL}" \
        --model "${MODEL}" \
        ${API_KEY_ARG} \
        --base-alias "${MODEL}" \
        --lora-alias "${LORA_ALIAS}" \
        --concurrency 4 8 \
        --lora-fractions 0.0 0.5 1.0 \
        --max-tokens 256 \
        --repeats 5 \
        --output "${BENCHMARK_DIR}/lora_mix.json"
    echo ""
else
    echo "Stage 4: SKIPPED — set LORA_ALIAS=<alias> to run LoRA mix test"
    echo "         (register an active adapter via POST /v1/adapters first)"
    echo ""
fi

# ── Stage 5: Prefix cache effectiveness ──────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "Stage 5: Prefix cache effectiveness (2K shared prefix)"
echo "────────────────────────────────────────────────────────────────"
python3 "${PROJECT_ROOT}/scripts/benchmark/prefix_cache.py" \
    --url "${BENCHMARK_URL}" \
    --model "${MODEL}" \
    ${API_KEY_ARG} \
    --shared-prefix-tokens 2048 \
    --max-tokens 256 \
    --output "${BENCHMARK_DIR}/prefix_cache.json"
echo ""

# ── Stage 6: FP8 KV cache experiment (manual step — see note) ─────────
echo "Stage 6: FP8 KV cache — MANUAL STEP"
echo "  Restart vLLM with --kv-cache-dtype fp8, then re-run stages 1-3."
echo "  See plans/phase-8-benchmarking-and-beta.md §8.5 Stage 6 for flags."
echo ""

# ── Stage 7: Overload + admission control ─────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "Stage 7: Overload and admission control stress test"
echo "────────────────────────────────────────────────────────────────"
python3 "${PROJECT_ROOT}/scripts/benchmark/overload.py" \
    --url "${BENCHMARK_URL}" \
    --model "${MODEL}" \
    ${API_KEY_ARG} \
    --rpm-limit 60 \
    --concurrent-limit 5 \
    --global-queue-limit 50 \
    --output "${BENCHMARK_DIR}/overload.json"
echo ""

# ── Generate capacity report ──────────────────────────────────────────
echo "────────────────────────────────────────────────────────────────"
echo "Generating capacity report → ${REPORT_PATH}"
echo "────────────────────────────────────────────────────────────────"
python3 "${PROJECT_ROOT}/scripts/benchmark/report.py" \
    --baseline   "${BENCHMARK_DIR}/baseline.json" \
    --concurrency "${BENCHMARK_DIR}/concurrency.json" \
    --context    "${BENCHMARK_DIR}/context_sweep.json" \
    --lora       "${BENCHMARK_DIR}/lora_mix.json" \
    --prefix     "${BENCHMARK_DIR}/prefix_cache.json" \
    --overload   "${BENCHMARK_DIR}/overload.json" \
    --report-path "${REPORT_PATH}"

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Phase 8 Benchmark Suite — COMPLETE                           ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Benchmark JSON files:"
ls -lh "${BENCHMARK_DIR}"/*.json 2>/dev/null | awk '{print "    " $NF " (" $5 ")"}'
echo ""
echo "  Capacity report: ${REPORT_PATH}"
echo ""
echo "  Next steps:"
echo "    1. Review docs/architecture/v1-capacity-report.md"
echo "    2. Fill in VRAM snapshot from: nvidia-smi in benchmarks/gpu-after-v1-load.txt"
echo "    3. Complete docs/runbooks/beta-checklist.md"
echo "    4. Run: INFERRA_INFERENCE_KEY=... pytest tests/integration -v"
