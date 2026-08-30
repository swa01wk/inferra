# Inferra Frontend Guide

> **`inferra-ui`** is the React SPA that ships with Inferra. It provides a ChatGPT-style
> inference playground and a control-plane dashboard — all five pages are wired directly
> to the FastAPI gateway's REST endpoints.
>
> **Status:** Implemented and E2E tested (2026-08-30). Browser → Vite proxy → gateway → Qwen3-4B on RunPod L4 confirmed working.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Node.js ≥ 18 | `node --version` |
| npm ≥ 9 | Bundled with Node.js |
| Inferra backend running | Gateway at `http://localhost:9100` |
| Inference key + Admin key | From `seed_dev_data.py` output |

---

## Starting the Frontend

```bash
cd inferra-ui

# First time: install dependencies
npm install

# Start the Vite dev server
npm run dev
```

Vite prints the local URL — typically `http://localhost:5173`. If that port is taken it
auto-increments to 5174, 5175, etc.

The dev server includes a **built-in proxy** that forwards all `/v1`, `/health`, and
`/metrics` requests to `http://localhost:9100`. This means:

- No CORS issues regardless of which port Vite picks.
- You never have to change the backend's CORS origins.
- The Gateway URL in Settings must be set to **the same origin as the browser** (e.g. `http://localhost:5173`).

---

## First-Time Configuration

1. Open the URL printed by `npm run dev` (e.g. `http://localhost:5173`).
2. Click **Settings** (gear icon, top-right corner).
3. Fill in the three fields:

   | Field | Value | Notes |
   |-------|-------|-------|
   | **Gateway URL** | `http://localhost:5173` | Must match the Vite URL in your browser bar |
   | **Inference Key** | `inf_...` | From `seed_dev_data.py` — used for Chat, Adapters, Usage |
   | **Admin Key** | `inf_...` | From `seed_dev_data.py` — used for Keys, Workers |

4. Click **Save**. All values are persisted in `localStorage` — you won't need to re-enter them on reload.

> **Note:** If you restart the Vite server and it picks a different port, update the Gateway URL to match. Both Inference Key and Admin Key stay valid across restarts.

---

## Pages

### Chat `/chat`

The primary view — a ChatGPT-style inference playground.

**Left sidebar controls:**

| Control | Default | Effect |
|---------|---------|--------|
| Model | Auto-selected from `/v1/models` | Which model alias to use |
| System prompt | `You are a helpful assistant.` | Prepended as `role: system` |
| Max tokens | 512 (slider: 64–2048) | Hard output cap (`max_tokens`) |
| Temperature | 0.7 (slider: 0–1.5) | Response randomness |
| Top P | 1.0 (slider: 0.01–1.0) | Nucleus sampling (`top_p`) |
| Streaming | ✅ On | Token-by-token SSE vs. full response |
| Thinking (Qwen3) | ✅ On | Qwen3's chain-of-thought reasoning mode |

**Thinking mode:**  
When enabled (default), Qwen3-4B emits `<think>…</think>` blocks before its answer.
The UI renders these as a collapsible violet **Thinking** block above each response —
click to expand/collapse the reasoning trace. Toggle OFF for clean, direct answers
(the backend sends `chat_template_kwargs: {enable_thinking: false}` to vLLM).

**Token stats bar** (bottom of chat area):  
Shows `Prompt tokens`, `Completion tokens`, `TTFT`, and `Total ms` after each response.
In streaming mode TTFT is measured client-side (first non-empty delta).

**Keyboard shortcut:** `Enter` to send, `Shift+Enter` for a newline in the input.

---

### API Keys `/keys`

Requires: **Admin key** in Settings.

- Lists all inference keys for your organization (name, prefix, type, status, expiry, created date).
- **New Key** button → modal with `name` + optional `expires_at` → `POST /v1/api-keys`.
- The secret is shown **once** with copy-to-clipboard and show/hide toggle. It cannot be retrieved again.
- **Revoke** button → `DELETE /v1/api-keys/{id}`. Only active, non-admin keys show the revoke button.

---

### LoRA Adapters `/adapters`

Requires: **Inference key** in Settings.

- Lists all adapters for your organization with their status badge, rank, storage URI, and any error message.
- **Register** button → modal:

  | Field | Notes |
  |-------|-------|
  | Name | Internal identifier (unique per org) |
  | Storage URI | MinIO/S3 path: `s3://inferra-adapters/<path>` |
  | Alias (optional) | Creates a `ModelAlias` — use this name as `model` in Chat |
  | Rank | 1–16 (enforced by backend; 16 matches vLLM `--max-lora-rank`) |

- While any adapter is in `registered / downloading / available` state, the page auto-polls every **3 s** — the status badge updates live until `active` or `failed`.

**Status badge colours:**

| Status | Colour | Meaning |
|--------|--------|---------|
| `registered` | Grey | Metadata accepted |
| `downloading` | Blue/pulse | Fetching from MinIO |
| `available` | Yellow | Downloaded, ready to load |
| `active` | Green | Serving requests |
| `failed` | Red | Error — hover for message |

---

### Usage `/usage`

Requires: **Inference key** in Settings.

- **Summary cards:** Total Requests, Prompt Tokens, Completion Tokens, Avg TTFT.
- **Latency chart:** bar chart of TTFT + Total ms for the last 20 requests (Recharts).
- **Request table:** click any row to expand a latency breakdown showing TTFT bar, Decode bar, Total bar, prompt/completion token counts, and inferred tokens/s.

Data comes from `GET /v1/usage` — returns the last 100 requests for your organization.

---

### Workers `/workers`

Requires: **Admin key** in Settings.

- Lists all registered GPU workers with hostname, GPU type, VRAM, endpoint URL, and status badge.
- For each worker with a running deployment, shows deployment config (dtype, context length, LoRA enabled, prefix caching).
- **Grafana Dashboard** section at the bottom: iframe embed of `$VITE_GRAFANA_URL` (default `http://localhost:3000`) with kiosk mode. Click **Open in new tab** for the full dashboard.

---

## Environment Variables

Stored in `inferra-ui/.env.local` (never committed):

```bash
# Target for the Vite dev-server proxy — never seen by the browser.
VITE_API_TARGET=http://localhost:9100

# Grafana URL for the iframe embed on the Workers page.
VITE_GRAFANA_URL=http://localhost:3000

# Leave VITE_GATEWAY_URL unset so the UI defaults to window.location.origin.
# Set it only when serving a built dist directly (not via the Vite dev server).
# VITE_GATEWAY_URL=http://localhost:9100
```

---

## Technology Stack

| Concern | Library | Version |
|---------|---------|---------|
| Framework | React + TypeScript | 19 |
| Build | Vite | 8 |
| Styling | Tailwind CSS | 3 |
| Routing | React Router | v6 |
| Inference | `openai` npm SDK | latest |
| Admin HTTP | axios | latest |
| Charts | Recharts | latest |
| Icons | lucide-react | latest |

---

## Project Layout

```
inferra-ui/
├── src/
│   ├── api/
│   │   ├── client.ts        # makeOpenAIClient / makeAdminClient / makeInferenceClient
│   │   └── types.ts         # TypeScript interfaces matching backend response shapes
│   ├── context/
│   │   └── AuthContext.tsx  # gatewayUrl + keys in localStorage
│   ├── components/
│   │   ├── layout/
│   │   │   ├── TopNav.tsx
│   │   │   └── SettingsModal.tsx
│   │   └── shared/
│   │       ├── Badge.tsx
│   │       ├── CopyButton.tsx
│   │       └── ErrorBanner.tsx
│   └── pages/
│       ├── chat/
│       │   ├── ChatPage.tsx
│       │   ├── MessageBubble.tsx
│       │   └── ThinkingBlock.tsx
│       ├── keys/KeysPage.tsx
│       ├── adapters/AdaptersPage.tsx
│       ├── usage/UsagePage.tsx
│       └── workers/WorkersPage.tsx
├── .env.local
├── vite.config.ts           # Vite proxy: /v1 → VITE_API_TARGET
├── package.json
└── tsconfig.json
```

---

## Backend Endpoint Map

| Page | Endpoints |
|------|-----------|
| Chat | `GET /v1/models`, `POST /v1/chat/completions` |
| Keys | `GET /v1/api-keys`, `POST /v1/api-keys`, `DELETE /v1/api-keys/{id}` |
| Adapters | `GET /v1/adapters`, `POST /v1/adapters`, `DELETE /v1/adapters/{id}`, `POST /v1/aliases` |
| Usage | `GET /v1/usage` |
| Workers | `GET /v1/workers`, `GET /v1/deployments` |

---

## Error Messages

| HTTP code | Displayed as |
|-----------|-------------|
| 401 | "Invalid API key — check Settings" |
| 429 | "Rate limited — retry in N seconds" (reads `Retry-After` header) |
| 503 | "Service temporarily at capacity — check Workers tab" |
| Network error | "Cannot reach gateway — is the tunnel running?" |

---

## Troubleshooting

**"Set your Inference Key in Settings" banner**  
→ Open Settings (gear icon), paste the inference key, Save.

**Chat sends a request but nothing comes back / CORS error in console**  
→ Gateway URL must match the Vite URL. If Vite is on `:5174`, set Gateway URL to `http://localhost:5174`.

**Workers page shows no workers / "Set your Admin Key"**  
→ Admin key is required. Paste it in Settings.

**Vite port keeps changing**  
→ Kill stale processes: `kill $(lsof -ti:5173) $(lsof -ti:5174)` then `npm run dev`.
