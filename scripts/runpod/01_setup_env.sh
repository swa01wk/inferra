#!/usr/bin/env bash
# Stage B + C — Create vLLM venv, install vLLM, configure HF cache, download Qwen3-4B
set -euo pipefail

echo "============================================"
echo " STAGE B: Python vLLM Environment Setup"
echo "============================================"

cd /workspace

echo ""
echo "--- Creating venv at /workspace/vllm-env ---"
python3 -m venv vllm-env
source vllm-env/bin/activate

echo ""
echo "--- Upgrading pip ---"
pip install --upgrade pip

echo ""
echo "--- Installing vLLM ---"
pip install vllm

echo ""
echo "--- vLLM version ---"
vllm --version

VLLM_VERSION=$(python3 -c "import vllm; print(vllm.__version__)")
echo "Installed vLLM version: $VLLM_VERSION"
echo "$VLLM_VERSION" > /workspace/vllm-version.txt

echo ""
echo "============================================"
echo " STAGE C: Hugging Face Cache + Qwen3-4B Download"
echo "============================================"

echo ""
echo "--- Creating persistent directories ---"
mkdir -p /workspace/huggingface-cache
mkdir -p /workspace/models
mkdir -p /workspace/adapters/test
mkdir -p /workspace/adapters/tenant-specific
mkdir -p /workspace/benchmarks
mkdir -p /workspace/logs
mkdir -p /workspace/inference-platform/apps
mkdir -p /workspace/inference-platform/tests
mkdir -p /workspace/inference-platform/scripts
mkdir -p /workspace/inference-platform/docs

echo ""
echo "--- Setting HF_HOME to /workspace/huggingface-cache ---"
export HF_HOME=/workspace/huggingface-cache

# Persist HF_HOME in bash profile
if ! grep -q "HF_HOME" ~/.bashrc 2>/dev/null; then
    echo 'export HF_HOME=/workspace/huggingface-cache' >> ~/.bashrc
fi
if ! grep -q "HF_HOME" ~/.profile 2>/dev/null; then
    echo 'export HF_HOME=/workspace/huggingface-cache' >> ~/.profile
fi

echo ""
echo "--- Disk space before download ---"
df -h /workspace

echo ""
echo "--- Saving idle GPU snapshot ---"
nvidia-smi > /workspace/benchmarks/gpu-before-vllm.txt
cat /workspace/benchmarks/gpu-before-vllm.txt

echo ""
echo "--- Installing huggingface_hub CLI ---"
pip install -U "huggingface_hub[cli]"

echo ""
echo "--- Downloading Qwen/Qwen3-4B to /workspace/models/Qwen3-4B ---"
echo "This will take several minutes (~8-10 GB download)..."
hf download Qwen/Qwen3-4B --local-dir /workspace/models/Qwen3-4B

echo ""
echo "--- Download complete. Verifying files ---"
du -sh /workspace/models/Qwen3-4B
ls -lah /workspace/models/Qwen3-4B

echo ""
echo "--- Disk space after download ---"
df -h /workspace

echo ""
echo "============================================"
echo " Stages B + C complete."
echo " vLLM env: /workspace/vllm-env"
echo " Model: /workspace/models/Qwen3-4B"
echo "============================================"
