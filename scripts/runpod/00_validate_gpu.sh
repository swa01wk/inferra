#!/usr/bin/env bash
# Stage A — Validate GPU, PyTorch CUDA, and workspace layout
set -euo pipefail

echo "============================================"
echo " STAGE A: GPU & Runtime Validation"
echo "============================================"

echo ""
echo "--- nvidia-smi ---"
nvidia-smi

echo ""
echo "--- PyTorch / CUDA ---"
python3 -c "
import torch
print('PyTorch :', torch.__version__)
print('CUDA available:', torch.cuda.is_available())
if torch.cuda.is_available():
    print('GPU       :', torch.cuda.get_device_name(0))
    props = torch.cuda.get_device_properties(0)
    print('VRAM (GB) :', round(props.total_memory / 1024**3, 2))
else:
    print('WARNING: CUDA not available!')
"

echo ""
echo "--- Python version ---"
python3 --version

echo ""
echo "--- Disk layout ---"
df -h

echo ""
echo "--- /workspace contents ---"
ls -lah /workspace || echo "(workspace empty or not mounted)"

echo ""
echo "============================================"
echo " Stage A complete. Check GPU is NVIDIA L4"
echo " with ~24 GB VRAM and CUDA is available."
echo "============================================"
