# End-to-End Integration Guide

> **Purpose:** Complete walkthrough for connecting all layers of Inferra:  
> RunPod vLLM → cloudflared tunnel → Mac Docker stack → FastAPI gateway → React frontend.  
> Use this guide at the start of every new GPU session.
>
> **Last validated:** 2026-08-30 — browser-to-GPU confirmed working end-to-end.

---

## Architecture Overview

```
Browser (inferra-ui)
  └── Vite dev proxy (/v1, /health, /metrics → :9100)
          ↓
Mac — Docker Compose
  ├── api-gateway :9100    ← FastAPI (auth, routing, metering, limits)
  ├── postgres             ← org, keys, adapters, usage_metrics
  ├── redis                ← rate limits, concurrency, quotas
  ├── minio                ← LoRA adapter artifacts
  ├── prometheus           ← metrics scrape
  └── grafana :3000        ← dashboards
          ↓ (per-request, via worker.endpoint in DB)
Cloudflared HTTPS tunnel (public URL)
          ↓
RunPod L4 GPU — vLLM :8000
  └── Qwen/Qwen3-4B BF16 · 8192 ctx · LoRA enabled · prefix cache
```

---

## Prerequisites Checklist

Before running `integrate.sh`, confirm all of the following:

- [ ] Docker Desktop is running on your Mac
- [ ] SSH key loaded: `ssh-add -l` shows `id_ed25519`
- [ ] RunPod pod `inferra-v1-migration` is **RUNNING** (not stopped)
- [ ] vLLM is serving on the pod (see §1 below)
- [ ] cloudflared tunnel is active on the pod (see §2 below)

---

## Part 1 — Pod Side (RunPod)

### 1a. Connect to the pod

```bash
# Copy the current SSH command from RunPod dashboard → Pod → Connect → SSH
# The container suffix changes on every restart.
export RUNPOD="jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io"
ssh -i ~/.ssh/id_ed25519 $RUNPOD
```

Attach to the persistent tmux session:

```bash
tmux attach -t inferra 2>/dev/null || tmux new -s inferra
```

### 1b. Check if vLLM is already running

```bash
# Quick health check
curl http://localhost:8000/health
```

- **Returns `{}`** → vLLM is running. Skip to §1c.
- **Connection refused** → start vLLM:

```bash
# Window 1 in tmux — start vLLM with V1 production flags
bash /workspace/scripts/04_finalize_phase1.sh
```

Wait for: `INFO: Application startup complete.` (~4–5 min on first load; ~2 min on warm restart).

### 1c. Check vLLM is serving the right model

```bash
curl -s http://localhost:8000/v1/models | python3 -m json.tool
# Expected: "id": "Qwen/Qwen3-4B"
```

**V1 production flags that must be active:**

| Flag | Value | Why |
|------|-------|-----|
| `--max-model-len` | `8192` | Full context window |
| `--dtype` | `bfloat16` | Memory-efficient, L4-compatible |
| `--gpu-memory-utilization` | `0.90` | Maximise KV cache |
| `--enable-lora` | — | LoRA adapter support |
| `--max-lora-rank` | `16` | Matches gateway `MAX_LORA_RANK` |
| `--max-loras` | `4` | Concurrent adapter slots |
| `--enable-prefix-caching` | — | Reduce TTFT on repeated prompts |

### 1d. Check if cloudflared tunnel is running

```bash
# Look for a running cloudflared process
tmux list-windows -t inferra
# Expected window: 'cf-tunnel'
```

If missing, start it (new tmux window):

```bash
tmux new-window -t inferra -n cf-tunnel
cloudflared tunnel --url http://localhost:8000
```

Wait for two lines:
```
INF  +-------------------------------------------------------------------+
INF  |  Your quick Tunnel has been created! Visit it at (it may take some minutes to be reachable):  |
INF  |  https://xxxx-yyyy-zzzz.trycloudflare.com                        |
INF  +-------------------------------------------------------------------+
```

**Copy the `https://xxxx.trycloudflare.com` URL** — you'll need it in §2.

> The URL is ephemeral and changes each time cloudflared restarts. Update `VLLM_PUBLIC_URL` on your Mac whenever it changes.

### 1e. Verify the tunnel is reachable from the pod

```bash
TUNNEL_URL=https://xxxx-yyyy-zzzz.trycloudflare.com   # replace with your URL
curl -s $TUNNEL_URL/health
# Expected: {}
```

---

## Part 2 — Mac Side

### 2a. Set the cloudflared URL

```bash
export VLLM_PUBLIC_URL=https://xxxx-yyyy-zzzz.trycloudflare.com
```

This env var is used by both `integrate.sh` and `docker-compose.real.yml`. Set it in every new terminal session before running the stack.

### 2b. Get the container ID

From RunPod dashboard → Pod → Connect → SSH. The hostname format is:
```
jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io
```
Extract just the `<CONTAINER_ID>` part (e.g. `64410f25`).

### 2c. Run `integrate.sh`

```bash
cd /path/to/inferra
./scripts/integrate.sh <CONTAINER_ID>
```

This single command does everything:

| Step | What happens |
|------|-------------|
| 1 | SSH key check |
| 2 | Verifies vLLM is reachable via `$VLLM_PUBLIC_URL` |
| 3 | Tears down old docker stack + volumes (clean slate) |
| 4 | Brings up all 7 containers (Postgres, Redis, MinIO, gateway, mock-vllm, Prometheus, Grafana) |
| 5 | Seeds dev org + admin key + inference key + model record + alias |
| 6 | Seeds real RunPod worker (retires mock, points `test-assistant` alias → real deployment) |
| 7 | Runs 32 integration tests (31 pass, 1 skipped) |
| 8 | Runs baseline benchmark: ~28 tok/s, TTFT ~610 ms |

At the end, the script prints:

```
  Gateway      : http://localhost:9100
  Inference key: inf_...
  Admin key    : inf_...
```

**Save these keys** — they're used in the frontend Settings.

### 2d. Verify the gateway

```bash
curl http://localhost:9100/health
# Expected: {"status": "ok", ...}

curl http://localhost:9100/v1/models \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY"
# Expected: {"data": [{"id": "test-assistant", ...}]}
```

---

## Part 3 — Frontend

### 3a. Start the Vite dev server

```bash
cd inferra-ui
npm install          # only needed once
npm run dev
```

Note the printed URL (e.g. `http://localhost:5173`).

### 3b. Configure Settings

Open the URL in your browser → click **Settings** (gear icon, top-right):

| Field | Value |
|-------|-------|
| **Gateway URL** | The URL shown in your browser bar (e.g. `http://localhost:5173`) — must match exactly so requests go through the Vite proxy |
| **Inference Key** | `inf_...` from `integrate.sh` output |
| **Admin Key** | `inf_...` from `integrate.sh` output |

Click **Save**.

### 3c. Send your first query

1. Click **Chat** in the top nav.
2. The model picker auto-loads and selects `test-assistant`.
3. Type a message and press **Enter**.
4. Watch the response stream token-by-token. The violet **Thinking** block shows Qwen3's reasoning trace (collapse it with the toggle).
5. After the response, the bottom bar shows `TTFT`, `Total ms`, and token counts.

---

## Resuming an Existing Session

If the pod was just restarted (container ID changed), only steps 2b–2c + 3a–3b are needed:

```bash
# 1. Get new container ID from RunPod dashboard
# 2. Start/verify vLLM + cloudflared on the pod (§1 above)
# 3. Get new cloudflared URL

export VLLM_PUBLIC_URL=https://<new-url>.trycloudflare.com
./scripts/integrate.sh <NEW_CONTAINER_ID>

cd inferra-ui && npm run dev
# Update Gateway URL in Settings if port changed
```

> The Postgres, Redis, and MinIO volumes are wiped by `integrate.sh` (step 3). Your inference and admin keys change every run — save the new values from the script output.

---

## Smoke Tests After Integration

Run these after `integrate.sh` to confirm everything works before using the UI:

```bash
# 1. Health
curl http://localhost:9100/health

# 2. Non-streaming chat
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"test-assistant","messages":[{"role":"user","content":"What is 2+2?"}],"max_tokens":32}'

# 3. Streaming chat
curl -N http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"test-assistant","messages":[{"role":"user","content":"Count to 5."}],"stream":true,"max_tokens":64}'

# 4. Usage (should show the two requests above)
curl http://localhost:9100/v1/usage \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY"
```

---

## Thinking Mode

Qwen3-4B has a built-in chain-of-thought reasoning mode. It is **enabled by default** — the model emits `<think>…</think>` blocks before its final answer.

**From the Chat UI:** use the **Thinking (Qwen3)** checkbox in the left sidebar.

**From the API directly:**
```bash
# Disable thinking for clean responses
curl http://localhost:9100/v1/chat/completions \
  -H "Authorization: Bearer $INFERRA_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test-assistant",
    "messages": [{"role": "user", "content": "What is the capital of France?"}],
    "max_tokens": 64,
    "enable_thinking": false
  }'
```

The gateway converts `enable_thinking` → `chat_template_kwargs: {enable_thinking: false}` before forwarding to vLLM.

---

## Port Reference

| Port | Service |
|------|---------|
| `9100` | API gateway (external, host) |
| `9000` | API gateway (internal, container) |
| `3000` | Grafana (admin / admin) |
| `9001` | MinIO console (minioadmin / minioadmin) |
| `5173–5175` | Vite dev server (frontend) |
| `8000` | vLLM on RunPod pod (internal) |

---

## Stopping

```bash
# Stop the frontend (Ctrl+C in the npm run dev terminal)

# Stop the docker stack (keeps volumes — data persists)
docker compose down

# OR wipe everything (next integrate.sh will re-seed)
docker compose down -v
```

**Stop the RunPod pod** from the dashboard to stop GPU billing ($0.50/hr):
```
RunPod dashboard → inferra-v1-migration → Stop
```

The workspace (`/workspace`) on the pod uses persistent storage — model weights, vLLM venv, and scripts survive the stop. Only GPU billing stops.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `integrate.sh` step 2 fails: "Could not reach vLLM" | cloudflared tunnel not running or URL changed | SSH to pod, check `tmux attach -t inferra:cf-tunnel`, get new URL |
| `integrate.sh` step 4 fails: container unhealthy | Docker Desktop not running | Start Docker Desktop, retry |
| Gateway returns 500 on chat | vLLM unreachable via cloudflared URL | Check tunnel; update `VLLM_PUBLIC_URL` and re-run integrate.sh |
| Frontend CORS error in browser console | Gateway URL in Settings doesn't match Vite URL | Set Gateway URL to the exact URL in the browser address bar |
| Thinking block never appears | `enable_thinking` OFF or model not Qwen3 | Check Thinking toggle in sidebar; confirm model is `test-assistant` |
| Workers page shows no workers | Admin key not set | Open Settings → paste Admin Key → Save |
| TTFT > 2 s on simple questions | Thinking mode ON + complex query | Toggle Thinking OFF for faster, direct responses |
