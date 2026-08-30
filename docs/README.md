# Inferra Documentation

> **Inferra** is a production-grade LLM inference platform — an OpenAI-compatible API gateway purpose-built around [vLLM](https://github.com/vllm-project/vllm), with multi-tenant isolation, LoRA adapter management, usage metering, rate limiting, and a full observability stack.

---

## Navigation

### Product
| Document | Description |
|----------|-------------|
| [Product Overview](product/overview.md) | What Inferra is, its goals, key capabilities, and V1 scope |

### Architecture
| Document | Description |
|----------|-------------|
| [System Architecture](architecture/system-architecture.md) | Full system design — components, data flows, infrastructure topology |
| [Data Model](architecture/data-model.md) | PostgreSQL schema, all tables, relationships, and design rationale |

### API Reference
| Document | Description |
|----------|-------------|
| [API Reference](api/api-reference.md) | Complete REST API — every endpoint, request/response schemas, examples |
| [Authentication](api/authentication.md) | API key types, how auth works, key lifecycle, security model |

### How-To Guides
| Document | Description |
|----------|-------------|
| [Getting Started](guides/getting-started.md) | First inference call in under 5 minutes |
| [**Frontend Guide**](guides/frontend-guide.md) | **Run the React UI, configure keys, use all 5 pages** |
| [LoRA Adapters](guides/lora-adapters.md) | Register, upload, and serve fine-tuned LoRA adapters |
| [Rate Limits & Quotas](guides/rate-limits-and-quotas.md) | Default limits, quota policies, 429/503 handling |
| [Observability](guides/observability.md) | Prometheus metrics, Grafana dashboards, OpenTelemetry tracing |

### Deployment
| Document | Description |
|----------|-------------|
| [Local Development](deployment/local-development.md) | Run the full stack locally with Docker Compose and mock vLLM |
| [**E2E Integration**](deployment/e2e-integration.md) | **Complete RunPod + gateway + frontend integration guide** |
| [RunPod GPU Deployment](deployment/runpod-gpu.md) | Provision an NVIDIA L4, serve real models, integrate with the gateway |

### Development
| Document | Description |
|----------|-------------|
| [Contributing & Testing](development/contributing.md) | Project layout, local dev setup, integration tests, benchmark scripts |

### Runbooks (Operations)
| Document | Description |
|----------|-------------|
| [How to Run All Phases](runbooks/how-to-run-all-phases.md) | **Start here** — end-to-end from pod to full benchmark run (Steps 0–6) |
| [RunPod POC Runbook](runbooks/runpod-poc-runbook.md) | SSH setup, tmux cheat sheet, GPU verification, vLLM management |
| [First L4 Deployment](runbooks/first-l4-deployment.md) | One-time pod provisioning walkthrough |
| [Beta Checklist](runbooks/beta-checklist.md) | Pre-beta readiness gate (13 sections) |
| [RunPod Networking RCA](runbooks/runpod-networking-rca.md) | SSH tunnel failures, cloudflared solution, future session checklist |

### Benchmark Scripts (`scripts/benchmark/`)
| Script | What it does |
|--------|-------------|
| `baseline.py` | Multi-profile TTFT + tokens/s (short/medium/long/2K + streaming) |
| `concurrency.py` | Async sweep: concurrency 1→2→4→8→16, stops at >2% error |
| `context_sweep.py` | 2K/4K/8K prompt × concurrency 1,4,8 — TTFT and KV pressure |
| `lora_mix.py` | Base vs LoRA TTFT at c=4 and c=8 |
| `prefix_cache.py` | Cold vs warm TTFT with 2K shared prefix |
| `overload.py` | 2× RPM burst + 3× concurrent burst; 429/503 pattern verification |
| `report.py` | Reads all JSON outputs → writes `docs/architecture/v1-capacity-report.md` |
| **`scripts/runpod/06_run_all_benchmarks.sh`** | **One-command orchestrator** — runs all 7 stages in sequence |

### Pod-Side Scripts (`scripts/runpod/`)
| Script | Stage | Purpose |
|--------|-------|---------|
| `00_validate_gpu.sh` | A | CUDA + GPU memory check |
| `01_setup_env.sh` | B | vLLM venv + pip install |
| `02_serve_vllm.sh` | D | Start 4K baseline vLLM |
| `03_validate_api.sh` | E | Non-streaming API smoke test |
| `04_finalize_phase1.sh` | G | Restart → 8K + LoRA + prefix cache + capture metrics |
| `05_validate_streaming.sh` | **F** | **TTFT measurement + incremental delivery validation** |
| `06_run_all_benchmarks.sh` | **8** | **Full Phase 8 benchmark suite orchestrator** |

---

## Quick Links

- **Start here (local):** [Getting Started](guides/getting-started.md)
- **Start here (GPU + UI):** [E2E Integration](deployment/e2e-integration.md)
- **Frontend UI:** [Frontend Guide](guides/frontend-guide.md)
- **API calls:** [API Reference](api/api-reference.md)
- **Current status:** [`STATUS.md`](../STATUS.md)
- **Phase plans:** [`plans/README.md`](../plans/README.md)
- **Benchmark capacity report:** [V1 Capacity Report](architecture/v1-capacity-report.md)
