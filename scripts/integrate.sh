#!/usr/bin/env bash
# Full integration runner — connects the local control plane to real vLLM on RunPod.
#
# Prerequisites:
#   1. RunPod pod is RUNNING and vLLM is serving (after 04_finalize_phase1.sh).
#   2. Docker Desktop is running on this Mac.
#   3. SSH key is loaded: ssh-add ~/.ssh/id_ed25519_runpod
#
# Usage:
#   ./scripts/integrate.sh <container-id>
#
# Get <container-id> from: RunPod dashboard → Pod → Connect → SSH
#   Example hostname: 5fmoz125ju1zc0-64410f27@ssh.runpod.io
#   Container ID:               ^^^^^^^^
#
# What this script does:
#   1. Opens SSH tunnel  Mac:8001 → RunPod:8000
#   2. Verifies vLLM is reachable through the tunnel
#   3. Tears down any old stack + volumes (clean start)
#   4. Brings up full docker-compose stack with real vLLM overlay
#   5. Seeds dev data (org, keys, model alias)
#   6. Seeds real worker (retires mock, points alias to real deployment)
#   7. Runs integration tests
#   8. Runs baseline benchmark through the gateway
#   9. Prints a summary

set -euo pipefail

POD_ID="5fmoz125ju1zc0"
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/id_ed25519_runpod}"
TUNNEL_LOCAL_PORT="8001"

# ── Parse args ────────────────────────────────────────────────────────
CONTAINER_ID="${1:-${RUNPOD_CONTAINER_ID:-}}"
if [[ -z "$CONTAINER_ID" ]]; then
    echo ""
    echo "Usage: $0 <container-id>"
    echo ""
    echo "Get container-id from RunPod dashboard → Connect → SSH"
    echo "Example: $0 64410f27"
    exit 1
fi
RUNPOD_HOST="${POD_ID}-${CONTAINER_ID}@ssh.runpod.io"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Inferra — Full Integration (Real vLLM + Control Plane)     ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  RunPod host : ${RUNPOD_HOST}"
echo "  SSH tunnel  : localhost:${TUNNEL_LOCAL_PORT} → RunPod:8000"
echo ""

# ── 1. Ensure SSH key is loaded ───────────────────────────────────────
echo "[ 1/8 ] SSH key check..."
if ! ssh-add -l 2>/dev/null | grep -q "id_ed25519_runpod"; then
    echo "  Loading SSH key..."
    ssh-add "${SSH_KEY}"
fi
echo "  SSH key loaded ✓"

# ── 2. Kill stale tunnel on port 8001 (if any) ───────────────────────
echo ""
echo "[ 2/8 ] SSH tunnel setup..."
# Kill any existing tunnel on this port
EXISTING_PID=$(lsof -ti tcp:${TUNNEL_LOCAL_PORT} 2>/dev/null || true)
if [[ -n "$EXISTING_PID" ]]; then
    echo "  Killing stale process on port ${TUNNEL_LOCAL_PORT} (pid ${EXISTING_PID})..."
    kill "$EXISTING_PID" 2>/dev/null || true
    sleep 1
fi

# Open fresh tunnel
ssh -i "${SSH_KEY}" \
    -L "${TUNNEL_LOCAL_PORT}:localhost:8000" \
    -N -f \
    -o "StrictHostKeyChecking=no" \
    -o "ServerAliveInterval=30" \
    "${RUNPOD_HOST}"

# Verify tunnel
echo "  Waiting for vLLM via tunnel..."
TUNNEL_OK=0
for i in $(seq 1 12); do
    if curl -sf "http://localhost:${TUNNEL_LOCAL_PORT}/health" > /dev/null 2>&1; then
        TUNNEL_OK=1
        break
    fi
    sleep 2
done
if [[ $TUNNEL_OK -eq 0 ]]; then
    echo ""
    echo "ERROR: Could not reach vLLM through tunnel."
    echo "  - Is the RunPod pod RUNNING?"
    echo "  - Is vLLM serving? (check: tmux attach -t inferra on the pod)"
    echo "  - Is the container ID correct? (current: ${CONTAINER_ID})"
    exit 1
fi
echo "  Tunnel to real vLLM is live ✓"
echo "  $(curl -s http://localhost:${TUNNEL_LOCAL_PORT}/v1/models | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  Model:", d["data"][0]["id"])' 2>/dev/null || echo '  (could not fetch model list)')"

# ── 3. Clean stack teardown ───────────────────────────────────────────
echo ""
echo "[ 3/8 ] Clean stack teardown (removes old volumes)..."
docker compose -f docker-compose.yml -f docker-compose.real.yml down -v --remove-orphans 2>/dev/null || true
echo "  Teardown complete ✓"

# ── 4. Bring up full stack ────────────────────────────────────────────
echo ""
echo "[ 4/8 ] Starting full stack (Postgres, Redis, MinIO, Mock-health, Gateway, Prometheus, Grafana)..."
docker compose -f docker-compose.yml -f docker-compose.real.yml up -d --build --wait
echo "  All services healthy ✓"

# ── 5. Seed dev data (org, api keys, model alias) ─────────────────────
echo ""
echo "[ 5/8 ] Seeding dev data..."
echo ""
SEED_OUTPUT=$(docker compose exec -T api-gateway python scripts/seed_dev_data.py 2>&1)
echo "$SEED_OUTPUT"

# Extract keys from seed output
INFERENCE_KEY=$(echo "$SEED_OUTPUT" | grep "^INFERENCE_KEY=" | cut -d= -f2)
ADMIN_KEY=$(echo "$SEED_OUTPUT" | grep "^ADMIN_KEY=" | cut -d= -f2)

if [[ -z "$INFERENCE_KEY" || -z "$ADMIN_KEY" ]]; then
    echo ""
    echo "ERROR: Could not extract API keys from seed output."
    echo "Seed output was:"
    echo "$SEED_OUTPUT"
    exit 1
fi
echo ""
echo "  INFERENCE_KEY : ${INFERENCE_KEY}"
echo "  ADMIN_KEY     : ${ADMIN_KEY}"

# ── 6. Seed real worker ───────────────────────────────────────────────
echo ""
echo "[ 6/8 ] Seeding real worker (retiring mock, pointing alias to real deployment)..."
docker compose exec -T api-gateway \
    env REAL_VLLM_ENDPOINT="http://host.docker.internal:${TUNNEL_LOCAL_PORT}" \
    python scripts/seed_real_worker.py
echo "  Real worker seeded ✓"

# ── 7. Integration tests ──────────────────────────────────────────────
echo ""
echo "[ 7/8 ] Running integration tests..."
INFERRA_INFERENCE_KEY="${INFERENCE_KEY}" \
INFERRA_ADMIN_KEY="${ADMIN_KEY}" \
INFERRA_BASE_URL="http://localhost:9100" \
    python -m pytest tests/integration -v --tb=short
echo "  Integration tests complete ✓"

# ── 8. Baseline benchmark through gateway ────────────────────────────
echo ""
echo "[ 8/8 ] Baseline benchmark (single request through gateway → real vLLM)..."
echo ""
python scripts/benchmark/baseline.py \
    --url "http://localhost:9100/v1/chat/completions" \
    --api-key "${INFERENCE_KEY}" \
    --prompt "Explain what a KV cache is in 2 sentences."

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Integration complete — all phases validated against real    ║"
echo "║  vLLM on RunPod L4                                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Gateway      : http://localhost:9100"
echo "  Grafana      : http://localhost:3000  (admin / admin)"
echo "  Inference key: ${INFERENCE_KEY}"
echo "  Admin key    : ${ADMIN_KEY}"
echo ""
echo "  Quick test:"
echo "    curl http://localhost:9100/v1/chat/completions \\"
echo "      -H 'Authorization: Bearer ${INFERENCE_KEY}' \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"model\":\"test-assistant\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"max_tokens\":64}'"
echo ""
echo "  Load test (locust):"
echo "    locust -f tests/load/locustfile.py --host http://localhost:9100 \\"
echo "      --api-key ${INFERENCE_KEY}"
echo ""
echo "  IMPORTANT: Stop the RunPod pod when done to stop GPU billing (\$0.50/hr):"
echo "    RunPod dashboard → inferra-v1-migration → Stop"
