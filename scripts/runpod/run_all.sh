#!/usr/bin/env bash
# Master runner: Stages A + B + C sequentially.
# Stage D (vLLM server) must be run separately in tmux/screen.
# Stage E + F (API validation) also runs separately once D is serving.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "##############################################"
echo "#  Inferra V1 POC — RunPod L4 Setup          #"
echo "##############################################"
echo ""

bash "$SCRIPT_DIR/00_validate_gpu.sh"
echo ""
bash "$SCRIPT_DIR/01_setup_env.sh"

echo ""
echo "##############################################"
echo " Setup complete! Next steps:"
echo ""
echo " 1. In THIS terminal (tmux window 1), start vLLM:"
echo "    bash ~/scripts/02_serve_vllm.sh"
echo ""
echo " 2. Open a NEW SSH session (tmux window 2),"
echo "    then validate the API:"
echo "    bash ~/scripts/03_validate_api.sh"
echo "##############################################"
