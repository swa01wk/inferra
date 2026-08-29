#!/usr/bin/env bash
# Run this script ON YOUR MAC to upload scripts and execute setup on RunPod.
# Requires: ssh-add ~/.ssh/id_ed25519_runpod  (enter passphrase once first)
set -euo pipefail

SSH_KEY="$HOME/.ssh/id_ed25519_runpod"
SSH_HOST="5fmoz125ju1zc0-64410f27@ssh.runpod.io"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Loading SSH key into agent (enter passphrase when prompted)..."
ssh-add "$SSH_KEY"

echo ""
echo "==> Uploading setup scripts to RunPod /root/scripts/ ..."
ssh "$SSH_HOST" "mkdir -p ~/scripts"
scp -i "$SSH_KEY" \
    "$SCRIPT_DIR/00_validate_gpu.sh" \
    "$SCRIPT_DIR/01_setup_env.sh" \
    "$SCRIPT_DIR/02_serve_vllm.sh" \
    "$SCRIPT_DIR/03_validate_api.sh" \
    "$SCRIPT_DIR/run_all.sh" \
    "${SSH_HOST}:~/scripts/"

echo ""
echo "==> Making scripts executable on remote..."
ssh "$SSH_HOST" "chmod +x ~/scripts/*.sh"

echo ""
echo "==> Running Stage A (GPU validation) remotely..."
ssh "$SSH_HOST" "bash ~/scripts/00_validate_gpu.sh"

echo ""
echo "============================================"
echo " GPU validated. Now running Stage B+C setup"
echo " (vLLM install + Qwen3-4B download ~10 min)"
echo "============================================"
echo ""
echo "==> To run Stage B+C + D + E+F, SSH in and run:"
echo "    ssh -i $SSH_KEY $SSH_HOST"
echo "    tmux new -s inferra"
echo "    bash ~/scripts/01_setup_env.sh        # B+C: install + download"
echo ""
echo "    # Then in tmux window 2 (Ctrl+B, c):"
echo "    bash ~/scripts/02_serve_vllm.sh       # D: start vLLM"
echo ""
echo "    # Then in tmux window 3 (Ctrl+B, c):"
echo "    bash ~/scripts/03_validate_api.sh     # E+F: API tests"
