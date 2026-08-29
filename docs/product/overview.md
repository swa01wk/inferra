# Inferra — Product Overview

## What Is Inferra?

Inferra is a **multi-tenant LLM inference platform** that wraps [vLLM](https://github.com/vllm-project/vllm) in a production-ready control plane. It exposes an **OpenAI-compatible API** while adding the enterprise primitives that vLLM alone does not provide: tenant isolation, API key management, LoRA adapter lifecycle management, usage metering, rate limiting, and a full observability stack.

The design philosophy is deliberately minimal for V1: one GPU worker (NVIDIA L4 24 GB), one model (Qwen/Qwen3-4B), proven scaling primitives — no premature abstraction. V1 establishes the operational baseline and data flywheel needed to make confident V2 capacity decisions.

---

## Problem Statement

Running vLLM directly is straightforward for a single user. Building a shared inference service on top of it requires:

- **Authentication** — who can call the endpoint, with what permissions?
- **Multi-tenancy** — how do different organizations share one GPU without leaking data or starving each other?
- **Fine-tuning integration** — how do customers bring their own LoRA adapters without requiring restarts?
- **Quotas and rate limits** — how do you prevent one tenant from exhausting GPU capacity?
- **Usage tracking** — what is each tenant consuming, at what latency, and at what cost?
- **Observability** — how do you know the system is healthy and where latency is coming from?

Inferra solves all of these at V1 scale.

---

## Key Capabilities

### OpenAI-Compatible API
Every inference call uses the same schema as the OpenAI Chat Completions API (`/v1/chat/completions`). Existing OpenAI SDK clients work without modification — simply point the base URL at the Inferra gateway.

Both **streaming** (Server-Sent Events) and **non-streaming** responses are supported.

### Multi-Tenant Isolation
Each **Organization** gets its own:
- Isolated API key namespace
- Per-tenant rate limits and quotas
- Separate usage records and metering
- Private LoRA adapter registry

Tenants cannot see or access each other's adapters, keys, or usage data.

### LoRA Adapter Management
Customers upload fine-tuned LoRA adapter weights to S3-compatible storage (MinIO in V1). The platform:
1. Downloads and validates the adapter artifact
2. Loads it into the running vLLM process via the vLLM LoRA API
3. Registers a **model alias** so the tenant can call it by name (e.g., `my-fine-tuned-assistant`)
4. Tracks adapter lifecycle: `registered → downloading → available → active`

Up to 4 concurrent adapters can be loaded with rank ≤ 16 in V1 configuration.

### Rate Limiting & Admission Control
Four layered controls prevent any single tenant from overwhelming the system:

| Control | Default | Enforcement |
|---------|---------|-------------|
| Requests per minute (RPM) | 60 | Redis token bucket (Lua script, atomic) |
| Max concurrent requests | 5 | Redis counter, per-tenant |
| Daily token quota | 1,000,000 | Redis daily counter |
| Global queue depth | 50 | System-wide gate before any request enters vLLM |

Over-limit requests receive `429 Too Many Requests` with a `Retry-After` header. System saturation returns `503 Service Unavailable`.

### Usage Metering & Request Tracing
Every request is recorded with full latency decomposition:

| Metric | Description |
|--------|-------------|
| `gateway_ms` | Time from receipt to routing start |
| `routing_ms` | Time to resolve model alias → worker |
| `ttft_ms` | Time to first token (from when request was forwarded to vLLM) |
| `decode_ms` | Time from first token to final token |
| `total_ms` | End-to-end wall clock |
| `prompt_tokens` | Tokens consumed in the prompt |
| `completion_tokens` | Tokens generated in the response |
| `tokens_per_second` | Decode throughput for this request |

Tenants can query their own usage via `GET /v1/usage`.

### Observability Stack
- **Prometheus** scrapes the gateway's `/metrics` endpoint and vLLM's native metrics
- **Grafana** provides pre-built dashboards for request rates, TTFT, token throughput, queue depth, and rate limit rejections
- **OpenTelemetry** distributed tracing is available (disabled by default) for gateway → vLLM span correlation

---

## V1 Scope & Constraints

| Item | V1 Value |
|------|----------|
| GPU | 1× NVIDIA L4 24 GB (RunPod) |
| Model | Qwen/Qwen3-4B · BF16 |
| Context window | 8,192 tokens max · 4,096 default |
| Max concurrent LoRA adapters | 4 |
| Max LoRA rank | 16 |
| Storage backend | MinIO (S3-compatible) |
| Database | PostgreSQL 16 |
| Cache / rate limiting | Redis 7 |
| API port | 9100 (gateway) |

**Out of scope for V1 (planned for V2+):**
- Multiple GPU workers / horizontal scaling
- FP8 KV-cache quantization
- Priority queues / SLO tiers
- Automatic adapter eviction (LRU)
- Frontend dashboard
- Billing integration

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Inference engine | [vLLM](https://github.com/vllm-project/vllm) 0.28.0 |
| API gateway | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| Database | PostgreSQL 16 + SQLAlchemy 2 (async) + Alembic |
| Rate limiting | Redis 7 (async, Lua scripts) |
| Adapter storage | MinIO (S3-compatible) + boto3 |
| Metrics | Prometheus + Grafana |
| Tracing | OpenTelemetry (OTLP) |
| Containerization | Docker Compose |
| GPU host | RunPod · NVIDIA L4 24 GB |

---

## High-Level Architecture Summary

```
Client (OpenAI SDK / curl)
        │  Bearer token
        ▼
┌─────────────────────────┐
│   Inferra API Gateway   │  FastAPI · port 9100
│  ┌─────────────────────┐│
│  │  Auth middleware     ││  SHA-256 key hash lookup → PostgreSQL
│  │  Admission control   ││  RPM + concurrency + quota → Redis
│  │  Routing resolver    ││  Model alias → Deployment → Worker
│  │  vLLM proxy client   ││  Streaming SSE pass-through
│  │  Usage recorder      ││  Async background task → PostgreSQL
│  └─────────────────────┘│
└──────────┬──────────────┘
           │ HTTP (internal)
           ▼
┌─────────────────┐     ┌──────────────┐     ┌──────────┐
│  vLLM 0.28.0    │     │  PostgreSQL  │     │  Redis   │
│  Qwen3-4B BF16  │     │  (metadata)  │     │  (limits)│
│  port 8000      │     └──────────────┘     └──────────┘
│  NVIDIA L4      │     ┌──────────────┐     ┌──────────┐
└─────────────────┘     │    MinIO     │     │Prometheus│
                        │  (adapters)  │     │ /Grafana │
                        └──────────────┘     └──────────┘
```

For the complete architecture document, see [System Architecture](../architecture/system-architecture.md).

---

## Design Principles

1. **OpenAI compatibility first** — zero client-side changes required to migrate from OpenAI.
2. **Fail open on infra failures, fail closed on security** — Redis/MinIO failures degrade gracefully; auth failures never do.
3. **Measure everything before optimizing** — V1 captures the full latency decomposition and benchmark matrix before any V2 investment.
4. **One worker, proven control plane** — horizontal scaling is a V2 concern; V1 proves the control plane design.
5. **Async throughout** — FastAPI + asyncpg + aioredis ensure gateway overhead is in the single-digit millisecond range.
