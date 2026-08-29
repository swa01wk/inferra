#!/usr/bin/env bash
# Stage D — Start vLLM serving Qwen3-4B (BF16, 4K context, 0.85 GPU util)
# Run this in a persistent terminal (tmux/screen) so it keeps running after SSH.
set -euo pipefail

echo "============================================"
echo " STAGE D: Start vLLM Inference Server"
echo "============================================"

export HF_HOME=/workspace/huggingface-cache
source /workspace/vllm-env/bin/activate

echo ""
echo "vLLM version: $(vllm --version)"
echo "Model path  : /workspace/models/Qwen3-4B"
echo "Config      : BF16 | max-model-len=4096 | gpu-mem-util=0.85"
echo ""
echo "Starting vLLM server on port 8000..."
echo "(Press Ctrl+C to stop)"
echo ""

vllm serve /workspace/models/Qwen3-4B \
    --served-model-name Qwen/Qwen3-4B \
    --dtype bfloat16 \
    --max-model-len 4096 \
    --gpu-memory-utilization 0.85 \
    --port 8000
