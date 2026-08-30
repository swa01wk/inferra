# RunPod vLLM Integration — Networking RCA & Lessons Learned

> **Purpose:** Documents every failure, root cause, and resolution encountered while
> connecting the Inferra control plane (Mac Docker Compose) to vLLM running on a RunPod
> L4 GPU pod. Use this as a troubleshooting reference for future sessions.
>
> **Session date:** 2026-08-30  
> **Pod:** `jgdi3n3khln553` · NVIDIA L4 24 GB · vLLM 0.28.0 · Qwen3-4B

---

## Summary of Issues Encountered

| # | Issue | Root Cause | Resolution |
|---|-------|-----------|------------|
| 1 | Stage G script timed out | vLLM startup > 180 s on 8K + LoRA + prefix cache | Wait — vLLM was still loading; just needed more time |
| 2 | SSH session dropped repeatedly | RunPod idle timeout kills interactive sessions | Use tmux; exit SSH immediately after sending commands |
| 3 | SSH `-N -f` port forwarding blocked | RunPod gateway blocks no-command mode | Tried multiple workarounds — all blocked |
| 4 | `sleep 86400` tunnel blocked | RunPod gateway rejects commands without PTY | Gateway requires PTY even for simple commands |
| 5 | `-t -L` PTY tunnel opened but port forward failed | RunPod gateway blocks TCP channel forwarding | `channel 3: open failed: unknown channel type` |
| 6 | RunPod proxy URL returned 404 | Port 8000 not exposed as HTTP in pod config | Would require pod stop + reconfigure |
| 7 | pyenv `.python-version` interference | `/Users/swa/Desktop/.python-version` pins Python 3.8 | Use `PYENV_VERSION=system python3` or run inside Docker |
| 8 | System Python missing pytest | macOS system Python has no third-party packages | Run pytest inside the `api-gateway` Docker container |
| **Final** | **cloudflared on pod** | — | **Creates public HTTPS URL; no SSH forwarding needed** |

---

## Issue 1 — Stage G Script Timed Out After 180 s

### What happened
`04_finalize_phase1.sh` waits up to 180 s for `GET /health` to return 200. With the
V1 production flags (8K context + LoRA + prefix caching), vLLM took longer than 180 s
to initialize, so the script exited with:

```
ERROR: vLLM did not become healthy within 180s.
Check logs: tmux select-window -t inferra:vllm-v1
```

### Root cause
8K context + `--enable-lora` + `--enable-prefix-caching` adds significant startup time:
- Safetensor weight loading from FUSE filesystem (~18 s for 3 shards)
- CUDA graph capture across 51 batch sizes (most time — ~2–3 min)
- LoRA engine initialisation

Total observed startup: ~4–5 minutes. Script timeout: 3 minutes.

### Resolution
The script exiting early did **not** kill vLLM. vLLM continued loading in its tmux window.

**Verification after script exits:**
```bash
# Keep polling until healthy
curl http://localhost:8000/health

# Or watch tmux window for startup complete message
tmux attach -t inferra:vllm-v1
# Wait for: Application startup complete.
```

### Fix applied to `04_finalize_phase1.sh`
Increase the health check timeout from 180 s to 300 s (36 → 60 attempts × 5 s).

### Key log line that confirms success
```
(APIServer pid=XXXX) INFO:     Application startup complete.
```

---

## Issue 2 — SSH Session Dropped Repeatedly

### What happened
Interactive SSH sessions to the pod kept disconnecting after ~5–10 minutes of
inactivity with:
```
Connection to ssh.runpod.io closed by remote host.
Connection to ssh.runpod.io closed.
```

### Root cause
RunPod's SSH gateway enforces aggressive idle timeouts on interactive sessions.
When the SSH session drops, any tmux session started **inside that SSH connection
without proper detach** also dies.

### Resolution
1. **Always use tmux** — start vLLM inside a named tmux window before exiting SSH.
2. **Send the command and exit immediately** — don't stay in the SSH session watching logs.
3. **Poll from Mac** instead of watching on the pod:

```bash
# Start vLLM in tmux and exit immediately
ssh -i ~/.ssh/id_ed25519 jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io
tmux new-session -d -s inferra -n vllm-v1 2>/dev/null || true
tmux send-keys -t inferra:vllm-v1 'source /workspace/vllm-env/bin/activate && \
  export HF_HOME=/workspace/huggingface-cache && \
  vllm serve /workspace/models/Qwen3-4B \
  --served-model-name Qwen/Qwen3-4B \
  --dtype bfloat16 --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --enable-lora --max-lora-rank 16 --max-loras 4 \
  --enable-prefix-caching' Enter
exit  # ← exit immediately; tmux keeps vLLM alive

# Poll health from Mac
for i in $(seq 1 60); do
  result=$(ssh -i ~/.ssh/id_ed25519 -o "StrictHostKeyChecking=no" \
    jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io \
    "curl -sf http://localhost:8000/health" 2>/dev/null)
  [[ -n "$result" ]] && echo "vLLM UP ✓" && break
  echo "attempt $i/60 — waiting 5s..." && sleep 5
done
```

### Important
tmux processes **survive SSH disconnection** as long as they were started with `tmux new-session -d`
(detached). vLLM running inside tmux will keep serving even after the SSH session closes.

---

## Issue 3 — SSH `-N -f` Port Forwarding Blocked

### What happened
`integrate.sh` used the standard SSH port forwarding approach:
```bash
ssh -i ~/.ssh/id_ed25519 \
    -L 8001:localhost:8000 \
    -N -f \
    -o "StrictHostKeyChecking=no" \
    jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io
```
This silently exited. Subsequent `curl http://localhost:8001/health` failed with:
```
curl: (56) Recv failure: Connection reset by peer
```

### Root cause
`-N` (do not execute a remote command) is blocked by RunPod's SSH gateway. The SSH
process appeared to start (port 8001 showed as listening via `lsof`) but the gateway
rejected the connection without forwarding, causing the connection reset.

### Resolution attempt
Replaced `-N` with `sleep 86400` to avoid the no-command restriction → led to Issue 4.

---

## Issue 4 — `sleep 86400` Tunnel Blocked (No PTY)

### What happened
```bash
ssh -i ~/.ssh/id_ed25519 \
    -L 8001:localhost:8000 \
    -f \
    jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io \
    "sleep 86400"
```
Returned immediately with:
```
Error: Your SSH client doesn't support PTY
```

### Root cause
RunPod's SSH gateway **requires a PTY (interactive terminal) for all connections**,
including those that run a simple command. The `-f` (background) flag prevents PTY
allocation, which the gateway rejects.

### Resolution attempt
Use `-t` to force PTY allocation → led to Issue 5.

---

## Issue 5 — `-t -L` PTY Tunnel: TCP Channel Forwarding Blocked

### What happened
```bash
ssh -i ~/.ssh/id_ed25519 -t \
    -L 8001:localhost:8000 \
    jgdi3n3khln553-<CONTAINER_ID>@ssh.runpod.io
```
This opened an interactive SSH session successfully (landed on the pod shell), but
port 8001 on Mac was NOT forwarding to the pod. The pod shell showed:
```
channel 3: open failed: unknown channel type: unsupported channel type
channel 3: open failed: unknown channel type: unsupported channel type
... (repeated for every curl attempt from Mac)
```
And from Mac:
```
curl: (56) Recv failure: Connection reset by peer
```

### Root cause
**RunPod's SSH proxy gateway fundamentally does not support TCP port forwarding.**
The `-L` flag requests a `direct-tcpip` channel from the SSH server. RunPod's gateway
proxy intercepts this and rejects it with `unsupported channel type` because their
gateway only proxies the interactive shell channel, not TCP forwarding channels.

This is a **hard RunPod gateway limitation** — no SSH forwarding approach will work.

### What this rules out permanently
- `ssh -L` (local port forwarding) — blocked
- `ssh -R` (remote port forwarding) — would also be blocked
- `ssh -D` (SOCKS proxy) — would also be blocked
- Any `-N`, `-f`, or background tunnel variant — blocked

---

## Issue 6 — RunPod HTTP Proxy URL Returned 404

### What happened
RunPod pods have an HTTP proxy at:
```
https://<pod-id>-<port>.proxy.runpod.net
```
Testing `https://jgdi3n3khln553-8000.proxy.runpod.net/health` returned HTTP 404.

### Root cause
The HTTP proxy only works for ports **explicitly configured as HTTP ports** in the pod's
template/settings in the RunPod dashboard. Port 8000 was not configured as an exposed
HTTP port when the pod was created.

### How to fix (for future pods)
In RunPod dashboard when creating or editing a pod:
- Add port `8000` as an **HTTP port** in the pod template
- After saving and restarting, `https://<pod-id>-8000.proxy.runpod.net` will work

### Why we didn't use this
Changing the exposed ports requires stopping and restarting the pod, which would kill
the running vLLM process and require another startup cycle. cloudflared was faster.

---

## Issue 7 — pyenv `.python-version` Interference on Mac

### What happened
`integrate.sh` ran `python -m pytest` on the Mac and got:
```
pyenv: version `3.8' is not installed (set by /Users/swa/Desktop/.python-version)
```

### Root cause
A `.python-version` file in `/Users/swa/Desktop/` instructs pyenv to use Python 3.8
for everything run inside that directory tree. Python 3.8 was not installed.

### Resolution
Two options:
1. Override pyenv for the command: `PYENV_VERSION=system python3 -m pytest ...`
2. Run tests inside the Docker container (preferred — same Python as production):

```bash
docker compose exec api-gateway \
  env INFERRA_INFERENCE_KEY=... INFERRA_ADMIN_KEY=... INFERRA_BASE_URL=http://localhost:9100 \
  python -m pytest tests/integration -v --tb=short
```

**Fix applied to `integrate.sh`:** All `python`/`python3` calls replaced with
`docker compose exec api-gateway python ...` so the container's Python 3.11 is always used.

---

## Issue 8 — macOS System Python Missing pytest

### What happened
After bypassing pyenv with `PYENV_VERSION=system python3`, tests still failed:
```
/Library/Developer/CommandLineTools/usr/bin/python3: No module named pytest
```

### Root cause
macOS ships a minimal Python (CommandLineTools) with no third-party packages.
pytest was not installed in it.

### Resolution
Run tests inside the `api-gateway` Docker container, which has all dependencies
from `requirements.txt` pre-installed:
```bash
docker compose exec api-gateway python -m pytest tests/integration -v --tb=short
```

---

## Final Working Solution — cloudflared Public Tunnel

### What it is
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
creates a secure, public HTTPS endpoint that tunnels traffic to a local port.
No SSH forwarding, no firewall rules, no pod reconfiguration needed.

### Setup (one-time per pod session)

On the pod:
```bash
# Download cloudflared binary
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -O /tmp/cloudflared && chmod +x /tmp/cloudflared

# Run in a dedicated tmux window
tmux new-window -t inferra -n cf-tunnel
tmux send-keys -t inferra:cf-tunnel \
  "/tmp/cloudflared tunnel --url http://localhost:8000 2>&1 | tee /tmp/cf-tunnel.log" Enter

# Get the public URL (wait ~5s for it to appear)
sleep 5 && grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cf-tunnel.log | head -1
```

Example URL: `https://mix-limousines-lopez-lincoln.trycloudflare.com`

### Verify from Mac
```bash
curl -s https://mix-limousines-lopez-lincoln.trycloudflare.com/health
# → {"status":"ok"}

curl -s https://mix-limousines-lopez-lincoln.trycloudflare.com/v1/models
# → {"object":"list","data":[{"id":"Qwen/Qwen3-4B",...}]}
```

### How integrate.sh uses it
```bash
# Run with default URL baked into the script:
./scripts/integrate.sh <CONTAINER_ID>

# Or override if the URL changes (new pod session = new URL):
VLLM_PUBLIC_URL=https://new-url.trycloudflare.com ./scripts/integrate.sh <CONTAINER_ID>
```

### Important: URL changes on every cloudflared restart
The `trycloudflare.com` URL is ephemeral — it changes every time cloudflared is restarted.
After a pod restart:
1. Restart vLLM in tmux
2. Restart cloudflared in tmux → get new URL
3. Update `VLLM_PUBLIC_URL` in `integrate.sh` and `docker-compose.real.yml`
4. Run `./scripts/integrate.sh <NEW_CONTAINER_ID>`

Or pass the new URL at runtime without editing files:
```bash
VLLM_PUBLIC_URL=https://new-url.trycloudflare.com ./scripts/integrate.sh <CONTAINER_ID>
```

---

## Checklist for Future Pod Sessions

```
□ 1. Start the pod — get new container ID from RunPod dashboard → Connect → SSH
□ 2. SSH in and check if vLLM tmux session is running:
       ssh -i ~/.ssh/id_ed25519 jgdi3n3khln553-<NEW_CONTAINER_ID>@ssh.runpod.io
       tmux attach -t inferra:vllm-v1
       curl http://localhost:8000/health

□ 3. If vLLM is down (always after pod restart):
       tmux send-keys -t inferra:vllm-v1 '<vllm serve command>' Enter
       # Wait 4-5 min for "Application startup complete."

□ 4. Restart cloudflared (always after pod restart):
       tmux new-window -t inferra -n cf-tunnel 2>/dev/null || true
       tmux send-keys -t inferra:cf-tunnel \
         '/tmp/cloudflared tunnel --url http://localhost:8000 2>&1 | tee /tmp/cf-tunnel.log' Enter
       sleep 5 && grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cf-tunnel.log | head -1

□ 5. Exit SSH (don't stay — idle timeout will kill the session)

□ 6. Update VLLM_PUBLIC_URL in integrate.sh and docker-compose.real.yml with the new URL

□ 7. On Mac:
       VLLM_PUBLIC_URL=https://<new-url>.trycloudflare.com ./scripts/integrate.sh <NEW_CONTAINER_ID>
```

---

## Architecture: Final Working Setup

```
RunPod Pod (NVIDIA L4)
├── tmux: vllm-v1
│   └── vllm serve Qwen3-4B  ← listening on localhost:8000
└── tmux: cf-tunnel
    └── cloudflared tunnel --url http://localhost:8000
            │
            │  Cloudflare Edge (HTTPS)
            ▼
https://mix-limousines-lopez-lincoln.trycloudflare.com
            │
            │  Public internet (HTTPS/443)
            ▼
Mac (Docker Compose)
├── api-gateway :9100
│   └── VLLM_BASE_URL = https://mix-limousines-lopez-lincoln.trycloudflare.com
├── postgres, redis, minio, prometheus, grafana
└── Worker.endpoint in DB = https://mix-limousines-lopez-lincoln.trycloudflare.com
    (resolver.py routes each inference request to this endpoint)
```

**No SSH tunnel required. No port forwarding required. No RunPod dashboard changes required.**
