# Inferra Frontend — V1 Specification

> **Implementation status: ✅ Complete** — `inferra-ui` implemented and E2E tested (2026-08-30).
> All five pages are live and wired to the real backend.
> See [`docs/guides/frontend-guide.md`](guides/frontend-guide.md) for the operational guide.
>
> This document is the original design specification. It is preserved as reference for
> future extensions (V2 items listed at the bottom).

---

## Backend Integration Points (Current Implementation)

| Backend item | Value |
|---|---|
| Gateway base URL | `http://localhost:9100` |
| Auth header | `Authorization: Bearer <inference-key>` |
| Admin auth header | `Authorization: Bearer <admin-key>` |
| Streaming | Server-Sent Events (`text/event-stream`) |
| OpenAI compatibility | Full — use the OpenAI JS/TS SDK, point `baseURL` at gateway |
| Context window | 8 192 tokens max (4 096 default) — enforced by gateway |
| Models endpoint | `GET /v1/models` |
| Chat endpoint | `POST /v1/chat/completions` |

---

## Recommended Tech Stack

| Concern | Choice | Reason |
|---------|--------|--------|
| Framework | React 18 + TypeScript | Component model suits chat + dashboard |
| Styling | Tailwind CSS + shadcn/ui | Rapid, consistent, dark-mode ready |
| Routing | React Router v6 | Simple SPA navigation |
| Inference SDK | `openai` npm package | Drop-in; set `baseURL` to gateway, streaming works |
| HTTP client | `fetch` / `axios` for admin calls | Standard; no extra dependency |
| State management | React Context + `useState` | Sufficient for V1 scope |
| Charts | Recharts | Lightweight; sufficient for usage graphs |
| Build | Vite | Fast dev server, minimal config |

**Install commands:**
```bash
npm create vite@latest inferra-ui -- --template react-ts
cd inferra-ui
npm install openai axios recharts react-router-dom
npx shadcn-ui@latest init
```

---

## Application Layout

```
inferra-ui/
  src/
    api/           # typed wrappers around the backend REST API
    components/    # shared UI components
    pages/         # one folder per route
      chat/        # ChatGPT-like playground  ← primary view
      keys/        # API key management
      adapters/    # LoRA adapter registry
      usage/       # per-request usage history
      workers/     # worker + deployment health
    context/       # AuthContext (stores key, org), SettingsContext
    App.tsx
    main.tsx
```

---

## Pages

### 1. Chat Playground `/chat` — Primary View

The main user-facing interface. Mirrors the ChatGPT UX while exposing
Inferra-specific controls (model alias selector, adapter routing, token usage).

#### Layout

```
┌─────────────────────────────────────────────────┐
│  Inferra   [Chat] [Keys] [Adapters] [Usage]      │  ← top nav
│            [Workers]  ·  ●  model: test-assistant │
├──────────┬──────────────────────────────────────┤
│          │                                      │
│ Settings │           Message thread             │
│  panel   │                                      │
│          │  [System]  You are a helpful...       │
│ Model ▼  │  [User]    What is 2+2?               │
│          │  [Asst]    The answer is 4.           │
│ max_tok  │                                      │
│ temp     │                                      │
│ stream ☑ │                                      │
│          ├──────────────────────────────────────┤
│          │  [ Type a message...          ] Send  │
└──────────┴──────────────────────────────────────┘
           │  Prompt: 12 tok | Completion: 8 tok  │
           │  TTFT: — ms | Total: — ms             │
```

#### Settings panel (left sidebar)

| Control | Type | Wires to |
|---------|------|----------|
| Model alias | Dropdown (populated from `GET /v1/models`) | `request.model` |
| System prompt | Textarea | `messages[0]` with `role: "system"` |
| Max tokens | Slider 64–2048 | `request.max_tokens` |
| Temperature | Slider 0.0–1.5 | `request.temperature` |
| Top-p | Slider 0.0–1.0 | `request.top_p` |
| Stream | Toggle | `request.stream` |
| Thinking (Qwen3) | Toggle | `request.enable_thinking` → `chat_template_kwargs` |

#### Message thread

- Renders `role: system / user / assistant` with distinct styling.
- **Streaming:** uses the `openai` SDK's `stream` iterator; assistant message grows token-by-token.
- **Thinking mode indicator:** Qwen3-4B default emits `<think>` tags — detect and render them in a collapsible block (similar to Claude's reasoning display).
- **Code blocks:** syntax-highlighted via `highlight.js` or `prism`.
- **Token bar (bottom):** shows `prompt_tokens`, `completion_tokens`, `ttft_ms`, `total_ms` pulled from the final SSE chunk's `usage` field.

#### Key implementation — streaming

```typescript
// src/api/chat.ts
import OpenAI from "openai";

export function createClient(baseURL: string, apiKey: string) {
  return new OpenAI({ baseURL, apiKey, dangerouslyAllowBrowser: true });
}

export async function* streamChat(
  client: OpenAI,
  messages: OpenAI.ChatCompletionMessageParam[],
  model: string,
  options: { maxTokens: number; temperature: number; topP: number }
) {
  const stream = await client.chat.completions.create({
    model,
    messages,
    stream: true,
    max_tokens: options.maxTokens,
    temperature: options.temperature,
    top_p: options.topP,
  });
  for await (const chunk of stream) {
    yield chunk.choices[0]?.delta?.content ?? "";
  }
}
```

#### Key implementation — Qwen3 thinking mode

```typescript
// Qwen3-4B emits <think>...</think> blocks before the actual answer.
// Strip or collapse them in the UI.
function parseThinking(content: string): { thinking: string; answer: string } {
  const match = content.match(/^<think>([\s\S]*?)<\/think>([\s\S]*)$/);
  if (!match) return { thinking: "", answer: content };
  return { thinking: match[1].trim(), answer: match[2].trim() };
}
```

---

### 2. API Keys `/keys`

Lets operators create and revoke inference keys for tenants. Maps to spec §18.

#### Layout

```
┌─────────────────────────────────────────────────┐
│ API Keys                            [+ New Key]  │
├────────────────┬──────────┬──────────┬──────────┤
│ Name           │ Prefix   │ Status   │ Actions  │
├────────────────┼──────────┼──────────┼──────────┤
│ dev-key        │ inf_abc1 │ ● active │ Revoke   │
│ staging-key    │ inf_xyz9 │ ○ revoked│ —        │
└────────────────┴──────────┴──────────┴──────────┘
```

#### "New Key" modal

Fields: `name`, `expires_at` (optional date picker).
On submit: `POST /v1/api-keys` (admin key required).
Response shows the **secret once** in a copy-to-clipboard box — warn the user it will not be shown again (spec §18.2).

#### API mapping

| Action | Method | Endpoint | Auth |
|--------|--------|----------|------|
| List keys | `GET` | `/v1/api-keys` | admin |
| Create key | `POST` | `/v1/api-keys` | admin |
| Revoke key | `DELETE` | `/v1/api-keys/{id}` | admin |

---

### 3. Adapters `/adapters`

LoRA adapter registry — core product differentiator (spec §13). Shows the adapter lifecycle
state machine and lets operators register new adapters with an alias.

#### Layout

```
┌─────────────────────────────────────────────────────┐
│ LoRA Adapters                          [+ Register]  │
├─────────────────┬───────┬─────────────┬─────────────┤
│ Name            │ Rank  │ Status      │ Alias       │
├─────────────────┼───────┼─────────────┼─────────────┤
│ finance-v1      │  16   │ ● active    │ finance-bot │
│ support-v2      │   8   │ ⟳ loading   │ support     │
│ legal-draft     │  16   │ ✗ failed    │ —           │
└─────────────────┴───────┴─────────────┴─────────────┘
```

#### Status badge colours

| State | Colour | Meaning |
|-------|--------|---------|
| `registered` | Grey | Metadata accepted, not yet downloaded |
| `downloading` | Blue | Artifact moving from MinIO to worker |
| `available` | Yellow | Validated, ready to load |
| `loaded` / `active` | Green | Serving requests |
| `failed` | Red | Error (shown in tooltip from `error_message`) |

#### "Register Adapter" modal

| Field | Description |
|-------|-------------|
| Name | Internal identifier |
| Storage URI | `s3://inferra-adapters/<path>` — MinIO bucket path |
| Base model | Pre-filled: `Qwen/Qwen3-4B` |
| Rank | Integer 1–16 (enforced by backend, §13.3) |
| Alias | Optional — creates a `ModelAlias` pointing to this adapter |

#### API mapping

| Action | Method | Endpoint | Auth |
|--------|--------|----------|------|
| List adapters | `GET` | `/v1/adapters` | inference |
| Register | `POST` | `/v1/adapters` | inference |
| Get status | `GET` | `/v1/adapters/{id}` | inference |
| Delete | `DELETE` | `/v1/adapters/{id}` | inference |
| Create alias | `POST` | `/v1/aliases` | inference |

**Auto-refresh:** poll `GET /v1/adapters` every 3 s while any adapter is in `downloading` / `loading` state so the status badge updates live.

---

### 4. Usage `/usage`

Per-request usage history (spec §20). Shows the full latency decomposition that distinguishes
Inferra from a raw API wrapper — gateway → routing → queue → TTFT → decode.

#### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Usage — last 100 requests                                        │
│  Total requests: 47 | Prompt tokens: 12,430 | Completion: 8,210 │
├──────────┬────────────────┬──────────┬───────┬────────┬─────────┤
│ Request  │ Model          │ Status   │ TTFT  │ Total  │ Tokens  │
├──────────┼────────────────┼──────────┼───────┼────────┼─────────┤
│ a1b2c3…  │ test-assistant │ ● done   │ 210ms │ 1.4s   │ 156     │
│ d4e5f6…  │ finance-bot    │ ● done   │ 340ms │ 2.1s   │ 289     │
│ 7g8h9i…  │ test-assistant │ ✗ failed │ —     │ 503ms  │ 0       │
└──────────┴────────────────┴──────────┴───────┴────────┴─────────┘
```

#### Latency breakdown (click row to expand)

```
Request a1b2c3…
  ├─ gateway_ms    12 ms   ← auth + middleware
  ├─ routing_ms     3 ms   ← alias → adapter → worker resolution
  ├─ ttft_ms      210 ms   ← time to first token (vLLM prefill + queue)
  ├─ decode_ms   1180 ms   ← generation time
  └─ total_ms    1405 ms   ← wall-clock end-to-end

  Prompt tokens: 48 | Completion tokens: 108 | Tokens/s: 91.5
```

#### Summary charts (Recharts)

- **Requests over time** — bar chart, last 60 minutes, 1-min buckets.
- **TTFT distribution** — histogram (P50 / P95 / P99 annotations).
- **Tokens per request** — stacked bar (prompt vs completion).

#### API mapping

| Action | Method | Endpoint | Auth |
|--------|--------|----------|------|
| Fetch usage | `GET` | `/v1/usage` | inference |

---

### 5. Workers `/workers`

Shows the GPU worker and active deployment — the data-plane health panel (spec §22).

#### Layout

```
┌─────────────────────────────────────────────────────────┐
│ Workers & Deployments                                    │
├──────────────────────────────────────────────────────────┤
│ ● Worker: RunPod L4 — NVIDIA L4 24 GB                   │
│   Endpoint:  http://host.docker.internal:8001            │
│   Status:    healthy                                     │
│   GPU:       NVIDIA L4 24 GB                            │
│                                                          │
│   Deployment: Qwen3-4B BF16 8K                          │
│   Model:      Qwen/Qwen3-4B                             │
│   Status:     running                                    │
│   Config:     dtype=bfloat16 · max_model_len=8192        │
│               enable_lora=true · prefix_caching=true     │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────┐
│ Grafana                      │  ← iframe embed
│ (live GPU / KV metrics)      │
│ http://localhost:3000/...    │
└──────────────────────────────┘
```

#### Grafana embed

Embed the provisioned dashboard as an iframe. Disable auth for the embed panel in Grafana
(`GF_AUTH_ANONYMOUS_ENABLED=true` in `docker-compose.yml`) or use a Grafana API key.

Key panels to surface directly:
- GPU utilization %
- VRAM used / total
- KV-cache utilization
- Requests in flight
- TTFT P95

#### API mapping

| Action | Method | Endpoint | Auth |
|--------|--------|----------|------|
| List workers | `GET` | `/v1/workers` | admin |
| List deployments | `GET` | `/v1/deployments` | admin |

---

### 6. Settings (modal / sidebar)

Global settings stored in `localStorage`:

| Setting | Default | Purpose |
|---------|---------|---------|
| Gateway URL | `http://localhost:9100` | Switch between local / remote |
| Inference API key | — | Stored locally for chat/usage calls |
| Admin API key | — | Stored locally for key/adapter/worker calls |
| Theme | dark | Light / dark toggle |

---

## Auth Flow in the UI

The frontend uses two keys (matching the backend's auth model from spec §18):

```
┌──────────────────────────────────────────────┐
│  Settings modal                              │
│                                              │
│  Inference Key: [inf_xxxx…………]  [Save]       │
│  Admin Key:     [inf_admin……]   [Save]       │
│  Gateway URL:   [http://localhost:9100]       │
└──────────────────────────────────────────────┘
         ↓ stored in localStorage
         ↓ injected into every API call via AuthContext
```

- **Inference key** → `Authorization` header on `/v1/chat/completions`, `/v1/usage`, `/v1/adapters`.
- **Admin key** → `Authorization` header on `/v1/api-keys`, `/v1/workers`, `/v1/deployments`, `/admin`.

No login page — V1 is operated by a single team with pre-shared keys.

---

## Component Breakdown

```
components/
  layout/
    TopNav.tsx          — nav links, active route highlight, model badge
    Sidebar.tsx         — chat settings panel
  chat/
    MessageThread.tsx   — scrollable message list
    MessageBubble.tsx   — user / assistant / system bubble with role badge
    ThinkingBlock.tsx   — collapsible <think>…</think> render
    InputBar.tsx        — textarea + send button + char counter
    TokenStats.tsx      — bottom bar: prompt tok / completion tok / TTFT / total ms
    ModelSelector.tsx   — dropdown populated from GET /v1/models
  usage/
    UsageTable.tsx      — sortable request table
    LatencyBreakdown.tsx — expandable row: gateway/routing/ttft/decode bars
    UsageSummary.tsx    — total requests / tokens cards
    UsageChart.tsx      — Recharts request rate + TTFT histogram
  adapters/
    AdapterTable.tsx    — list with status badges + auto-refresh
    RegisterModal.tsx   — form: name / storage_uri / rank / alias
    StatusBadge.tsx     — colour-coded state pill
  keys/
    KeyTable.tsx        — list with revoke action
    CreateKeyModal.tsx  — name + expiry + secret-once display
  workers/
    WorkerCard.tsx      — worker + deployment detail
    GrafanaEmbed.tsx    — iframe wrapper with auth skip
  shared/
    CopyButton.tsx      — copy-to-clipboard for secrets
    ErrorBanner.tsx     — 401 / 429 / 503 user-friendly messages
    StreamingIndicator.tsx — animated dots while generating
```

---

## Error Handling (Aligned with Backend)

| HTTP status | Backend source | UI behaviour |
|-------------|---------------|--------------|
| `401` | Invalid / expired key | Banner: "Invalid API key — check Settings" |
| `403` | Admin key on inference route | Banner: "This key type cannot be used here" |
| `400` | Context too long | Inline: "Prompt + max_tokens exceeds 8 192 — reduce either" |
| `422` | Adapter rank > 16 | Form error: "Rank exceeds the 16-rank policy limit" |
| `429` | RPM / concurrent / quota | Banner: "Rate limited — retry in N seconds" (reads `Retry-After` header) |
| `503` | vLLM not ready / queue full | Banner: "Inference unavailable — check Workers tab" |
| Network error | Tunnel down | Banner: "Cannot reach gateway — is the SSH tunnel running?" |

---

## Key Pages → Backend Endpoint Map (Quick Reference)

| Page | Endpoints used |
|------|---------------|
| Chat | `GET /v1/models`, `POST /v1/chat/completions` |
| Keys | `GET /v1/api-keys`, `POST /v1/api-keys`, `DELETE /v1/api-keys/{id}` |
| Adapters | `GET /v1/adapters`, `POST /v1/adapters`, `DELETE /v1/adapters/{id}`, `POST /v1/aliases` |
| Usage | `GET /v1/usage` |
| Workers | `GET /v1/workers`, `GET /v1/deployments` |
| Settings | `GET /health` (ping on save to validate key/URL) |

---

## Spec Alignment Checklist

| Spec section | Frontend coverage |
|---|---|
| §1 Executive Summary | Chat playground exposes the OpenAI-compatible surface |
| §9.1 Inference API | `POST /v1/chat/completions` + streaming wired in Chat page |
| §9.2 Adapter API | Full CRUD in Adapters page |
| §9.3 Admin & Usage | Keys page (create/revoke) + Usage page + Workers page |
| §9.4 Model Alias | Model selector in chat populated from `GET /v1/models`; alias resolution is transparent |
| §13 LoRA Adapters | Register modal covers storage_uri, rank, alias; status machine shown as badges |
| §13.2 Adapter lifecycle | `registered → downloading → available → loaded → active → failed` shown with auto-refresh |
| §18 Multi-tenancy / Auth | Two-key model in Settings (inference + admin); error banners for 401/403 |
| §19 Rate limits | 429 / 503 surfaced with `Retry-After` countdown |
| §20 Usage metering | Full latency breakdown per request (gateway / routing / ttft / decode / total) |
| §21 Observability | Grafana iframe embed on Workers page; summary charts in Usage |
| §23 Failure modes | Every error code mapped to a user-friendly message |
| §34 Success criteria | Chat validates streaming; isolation validated by key model; usage proves traceability |

---

## Scaffold Commands

```bash
# Create the Vite + React + TypeScript project
npm create vite@latest inferra-ui -- --template react-ts
cd inferra-ui

# Dependencies
npm install openai axios recharts react-router-dom lucide-react
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p

# shadcn/ui component library
npx shadcn-ui@latest init
npx shadcn-ui@latest add button input textarea select badge card table dialog tabs

# Dev server (points at the running gateway)
VITE_GATEWAY_URL=http://localhost:9100 npm run dev
```

The frontend can be added to the existing Docker Compose stack:

```yaml
# Append to docker-compose.yml
  inferra-ui:
    build:
      context: inferra-ui
      dockerfile: Dockerfile
    ports:
      - "5173:5173"
    environment:
      VITE_GATEWAY_URL: http://api-gateway:9000
    depends_on:
      - api-gateway
```

---

## Environment Variables

```bash
# inferra-ui/.env.local
VITE_GATEWAY_URL=http://localhost:9100
VITE_GRAFANA_URL=http://localhost:3000
```

Both are injected at build time by Vite — no secrets in the frontend bundle. API keys are stored
only in `localStorage` and never committed to source.

---

## V2 Extensions (after beta feedback)

| Feature | Trigger |
|---------|---------|
| Multi-org switcher | Multiple tenants onboarded |
| Adapter upload UI | MinIO drag-and-drop to avoid manual S3 URI entry |
| Live token streaming visualization | User request for rate/throughput visibility |
| Conversation history persistence | Usage table linked to replay a past request |
| Grafana panel deep links | Click a metric → jump to Grafana panel |
| Dark/light theme toggle | User preference |
| Mobile responsive layout | Mobile traffic detected |
