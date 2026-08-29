# Mini-Predibase Inference Platform — Implementation Plan

## Product Summary

A single-GPU inference control plane wrapping vLLM, serving Qwen3-4B with multi-tenant API keys, LoRA adapter routing, usage metering, and full observability. Runs on one NVIDIA L4 24 GB in RunPod via Docker Compose.

**Governing constraint:** One L4 · One Base Model · Many Users · Many LoRA Adapters

---

## Phase Overview

| Phase | Name | Spec Milestones | Key Output |
|-------|------|-----------------|------------|
| 1 | Infrastructure & Runtime Foundation | M1 | vLLM + Qwen3-4B running, memory baseline |
| 2 | FastAPI API Gateway | M2 | OpenAI-compatible proxy in front of vLLM |
| 3 | Identity & Authentication | M3 | Tenants, API keys, hashed auth middleware |
| 4 | Usage Metering & Request Tracing | M4 | Every request has a durable, traced usage record |
| 5 | LoRA Adapter System | M5 | Adapter registry, aliases, multi-LoRA routing |
| 6 | Rate Limits & Admission Control | M6 | Redis-backed quotas, predictable 429 behaviour |
| 7 | Observability Stack | M7 | Prometheus + Grafana + OpenTelemetry wired up |
| 8 | Benchmarking & Beta Hardening | M8 + M9 | Load-tested capacity report, beta-ready product |

> **Milestone numbering note:** Two source documents use slightly different M-numbers for M5–M9.
> The table above follows the **main spec** (`Mini_Predibase_Inference_Platform_V1_Detailed_Specification.docx`).
> The POC guide splits Phase 5 into M5 (Aliases) and M6 (LoRA), shifting Limits→M7, Observability→M8, Load test→M9.
> These plans reconcile both by merging POC-guide M5+M6 into one Phase 5 (LoRA Adapter System)
> and combining load test + beta into Phase 8, which covers all content from both documents.

---

## Dependency Graph

```
Phase 1 (Runtime)
    └── Phase 2 (Gateway)
            ├── Phase 3 (Identity)  ──────────────────────────────┐
            │       └── Phase 4 (Usage)                          │
            │               └── Phase 5 (Adapters) ─────────────┤
            │                       └── Phase 6 (Limits) ────────┤
            │                               └── Phase 7 (Observ) ┤
            │                                       └── Phase 8 (Beta)
            └── (all phases depend on gateway being stable)
```

---

## Repository Structure (target)

```
inference-platform/
  apps/
    api/
      main.py
      routes/          # chat, completions, adapters, keys, usage, health
      middleware/       # auth, rate-limit, logging
      schemas/          # Pydantic request/response models
      services/
        auth/
        routing/
        adapters/
        deployments/
        usage/
        limits/
        observability/
        workers/
        vllm/
        config/
        health/
  db/
    models/             # SQLAlchemy ORM models
    migrations/         # Alembic migrations
  infra/
    docker/
    prometheus/
    grafana/
  tests/
    unit/
    integration/
    load/
  scripts/
    benchmark/
    adapters/
  docs/
    architecture/
    runbooks/
```

---

## Technology Stack

| Concern | Technology |
|---------|-----------|
| Inference runtime | vLLM (OpenAI-compatible mode) |
| Base model | Qwen/Qwen3-4B (BF16) |
| API gateway | FastAPI + Uvicorn |
| Metadata store | PostgreSQL |
| Hot state / rate limits | Redis |
| Adapter / model artifacts | S3 or MinIO |
| Metrics | Prometheus |
| Dashboards | Grafana |
| Distributed tracing | OpenTelemetry |
| Container orchestration | Docker Compose (no Kubernetes in V1) |
| GPU | NVIDIA L4 24 GB on RunPod |

---

## Key Design Principles

- vLLM owns the data plane. The platform owns the control plane.
- Never store plaintext API keys. Return secret once at creation.
- Measure before scaling. Telemetry and real traffic drive V2 decisions.
- Fail predictably: 429/503 under overload, not silent queue growth.
- Adapter storage path is never exposed to clients.
- Administrative endpoints use separate auth from inference keys.
- Docker Compose is the right complexity for one GPU worker.

---

## Phase Files

- [Phase 1 — Infrastructure & Runtime Foundation](phase-1-infrastructure-and-runtime.md)
- [Phase 2 — FastAPI API Gateway](phase-2-api-gateway.md)
- [Phase 3 — Identity & Authentication](phase-3-identity-and-authentication.md)
- [Phase 4 — Usage Metering & Request Tracing](phase-4-usage-metering-and-tracing.md)
- [Phase 5 — LoRA Adapter System](phase-5-lora-adapter-system.md)
- [Phase 6 — Rate Limits & Admission Control](phase-6-rate-limits-and-admission-control.md)
- [Phase 7 — Observability Stack](phase-7-observability-stack.md)
- [Phase 8 — Benchmarking & Beta Hardening](phase-8-benchmarking-and-beta.md)
