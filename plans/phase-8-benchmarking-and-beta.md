# Phase 8 — Benchmarking & Beta Hardening

**Spec Milestones:** M8 — Load Test · M9 — Beta  
**Exit Criterion:** Benchmarked concurrency envelope for 4K and 8K context is documented. Graceful overload behavior is verified. A small external cohort can use the product safely.

---

## Goals

- Execute the full benchmark matrix from spec section 24.
- Document the safe service envelope (concurrent users, context lengths, LoRA mix).
- Harden security for beta: TLS, secret rotation, input limits, dependency pinning.
- Build a minimal admin UI (or CLI) for tenant/key/adapter management.
- Harden persistence: confirm PostgreSQL backups, adapter artifact durability.
- Verify all failure modes from spec section 23 behave as specified.
- Write the V1 capacity report that governs V2 investment decisions.

---

## Deliverables

1. `scripts/benchmark/` — full benchmark suite.
2. `docs/architecture/v1-capacity-report.md` — benchmarked service envelope.
3. `apps/api/routes/admin_ui.py` or separate `apps/admin/` — basic management UI.
4. `docs/runbooks/beta-checklist.md` — operational readiness checklist.
5. `infra/docker/nginx.conf` — TLS termination config.
6. `tests/integration/` — integration test suite covering all API surfaces.
7. `tests/load/` — locust or k6 load test scripts.

---

## Part A: Benchmarking

### 8.1 Benchmark Suite Structure

```
scripts/benchmark/
  baseline.py              # Phase 1 single-request (re-run with full stack)
  concurrency.py           # multi-user concurrent tests
  context_sweep.py         # 2K / 4K / 8K context tests
  lora_mix.py              # base model vs LoRA request mix
  prefix_cache.py          # shared-prefix workload
  overload.py              # admission control and 429 behaviour
  report.py                # aggregate results into markdown table
```

### 8.2 Benchmark Profiles (Spec Section 24.1)

| Profile | Input Tokens | Output Tokens | Purpose |
|---------|-------------|---------------|---------|
| Short chat | 256–512 | 128 | Interactive latency baseline |
| Medium chat | ~1K | 256 | Typical assistant workload |
| Long prompt | ~4K | 256 | KV-cache and prefill stress |
| Upper V1 context | ~8K | 256 | Capacity boundary |
| Shared-prefix | 2K static + small suffix | 256 | Prefix-cache effectiveness |
| Multi-LoRA mix | 1K mixed adapters | 256 | Adapter-aware batching |

### 8.3 Concurrency Matrix (Spec Section 15.2)

Run each profile at increasing concurrency levels:

| Concurrency | Prompt Profiles | Output Profiles | What to Measure |
|-------------|----------------|-----------------|-----------------|
| 1, 2, 4 | 512 / 1K / 2K | 128 / 256 / 512 | Baseline latency and tokens/s |
| 8 | 1K / 2K / 4K | 128 / 256 | Batching efficiency and TTFT |
| 16 | 1K / 2K / 4K | 128 / 256 | Queue growth, KV pressure, P95 |
| 32 | Mixed | Mixed | Overload behaviour (only if 16 is safe) |

### 8.4 Required Benchmark Metrics (Spec Section 24.2)

For each cell in the matrix, capture:

- TTFT P50 / P95 / P99 (milliseconds)
- Total latency P50 / P95 / P99 (milliseconds)
- Output tokens/second per request and aggregate
- Requests/second or completed requests/minute
- Queue wait time and queue depth (from vLLM metrics)
- GPU utilization % and VRAM utilization %
- KV-cache utilization / capacity pressure
- Prefix-cache hit ratio
- Adapter load latency (for LoRA mix tests)
- Error / OOM / rejection rate

### 8.5 Benchmark Stages (Spec Section 24.3)

Execute in order:

```
Stage 1: Single-request quality and latency baseline
    - Run baseline.py with each prompt profile
    - Record: TTFT, total latency, tokens/s, VRAM after warmup

Stage 2: Concurrency sweep 2 → 4 → 8 → 16 → (32 only if 16 is clean)
    - Use concurrency.py with medium-chat profile first
    - Record: all required metrics at each level
    - Stop if error rate > 2% or P99 TTFT > 10s

Stage 3: Context sweep 4K vs 8K
    - Use context_sweep.py at concurrency=4 and concurrency=8
    - Record: VRAM delta, KV-cache saturation, TTFT change

Stage 4: Base model vs LoRA request mix
    - Use lora_mix.py: 50/50 split base vs LoRA at concurrency=4 and 8
    - Record: latency difference, adapter load event impact

Stage 5: Shared-prefix workloads
    - Use prefix_cache.py: same 2K system prompt, varied user suffix
    - Record: prefix_cache_hit_ratio, TTFT improvement

Stage 6: FP8 KV cache experiment (spec section 17 — V1 benchmark, not shipped default)
    Prerequisites: Stages 1–5 complete and BF16 numbers recorded.
    This is NOT the shipped V1 configuration. It is a benchmarked comparison
    to quantify capacity gain. BF16 remains the production default.

    6a. Record the BF16 reference numbers from Stage 2 (already done).

    6b. Restart vLLM with FP8 KV cache:
        vllm serve /workspace/models/Qwen3-4B \
          --served-model-name Qwen/Qwen3-4B \
          --dtype bfloat16 \
          --kv-cache-dtype fp8 \
          --max-model-len 8192 \
          --gpu-memory-utilization 0.90 \
          --enable-lora --max-lora-rank 16 --max-loras 4 \
          --enable-prefix-caching \
          --host 0.0.0.0 --port 8000

        Note: FP8 KV requires a compatible CUDA environment. If the installed
        vLLM version does not support --kv-cache-dtype, skip and document as
        "version constraint — revisit with next vLLM upgrade."

    6c. Re-run concurrency=1, 4, 8 with 4K and 8K context profiles.

    6d. Fill in the FP8 comparison table in v1-capacity-report.md:

    | Dimension        | BF16 baseline | FP8 experiment |
    |------------------|---------------|----------------|
    | KV element width | 16-bit        | 8-bit          |
    | VRAM used (idle) | record        | record         |
    | Max KV tokens    | record        | record         |
    | TTFT P95 (c=4)   | record        | record         |
    | Tokens/s (c=4)   | record        | record         |
    | Quality change   | baseline      | evaluate        |

    6e. Decision gate: if FP8 quality is acceptable and capacity gain > 15%,
        flag for V2 as a candidate default. Otherwise document and close.

Stage 7: Overload and admission control tests
    - Use overload.py: ramp to 2× rate limit, verify 429 pattern
    - Verify no OOM, no queue growth beyond configured limit
    - Verify TTFT for accepted requests does not degrade severely
```

### 8.6 Locust / k6 Load Test Script

Example `tests/load/locustfile.py`:

```python
from locust import HttpUser, task, between

class InferenceUser(HttpUser):
    wait_time = between(1, 3)
    host = "https://your-platform-host"

    def on_start(self):
        self.headers = {"Authorization": f"Bearer {API_KEY}"}

    @task(3)
    def short_chat(self):
        self.client.post("/v1/chat/completions", headers=self.headers, json={
            "model": "test-assistant",
            "messages": [{"role": "user", "content": SHORT_PROMPT}],
            "max_tokens": 128,
            "stream": False,
        })

    @task(1)
    def medium_chat(self):
        self.client.post("/v1/chat/completions", headers=self.headers, json={
            "model": "test-assistant",
            "messages": [{"role": "user", "content": MEDIUM_PROMPT}],
            "max_tokens": 256,
            "stream": False,
        })
```

### 8.7 Capacity Report Template

`docs/architecture/v1-capacity-report.md` must document:

```
## V1 Safe Service Envelope — Qwen3-4B on NVIDIA L4 24 GB

### Memory at Startup
- Weight allocation: X GB
- Runtime overhead: X GB
- KV-cache pool: X GB

### Latency at Concurrency N (4K context)
| Concurrency | TTFT P50 | TTFT P95 | Total P95 | Tokens/s |
|-------------|----------|----------|-----------|----------|
| 1           |          |          |           |          |
| 4           |          |          |           |          |
| 8           |          |          |           |          |
| 16          |          |          |           |          |

### Recommended Operating Points
- Interactive (<500ms TTFT): concurrency <= X
- Batch/async (<2s TTFT): concurrency <= X
- Maximum safe concurrency: X at 4K context

### FP8 KV Cache Experiment Results (Stage 6)

| Dimension | BF16 baseline | FP8 experiment |
|-----------|---------------|----------------|
| KV element width | 16-bit | 8-bit |
| VRAM at idle (after model load) | PENDING | PENDING |
| Max cacheable tokens (estimated) | PENDING | PENDING |
| TTFT P95 at concurrency=4, 4K ctx | PENDING | PENDING |
| TTFT P95 at concurrency=4, 8K ctx | PENDING | PENDING |
| Output tokens/s at concurrency=4 | PENDING | PENDING |
| Quality delta (subjective eval) | baseline | PENDING |
| Decision | ship as V1 default | promote to V2 default if gain > 15% |

### V2 Investment Triggers (from spec section 27)
- [ ] GPU saturation observed at N concurrent users
- [ ] Adapter load latency > Xs at Y adapters
- [ ] Long contexts (>8K) dominate traffic
```

---

## Part B: Beta Hardening

### 8.8 Security Hardening (Spec Section 30)

| Requirement | Implementation |
|-------------|---------------|
| TLS for all external traffic | Nginx reverse proxy with Let's Encrypt cert in front of Docker Compose |
| Secret rotation | `POST /v1/api-keys` + `DELETE /v1/api-keys/{id}` (zero-downtime key rotation) |
| Secrets via env/secret store | Docker secrets or `.env` file, never committed to source |
| Tenant ownership checks | Already in Phase 5; audit in this phase |
| Artifact validation before LoRA load | Phase 5 validator; re-verify with adversarial inputs |
| Input size limits | Phase 6 ceiling; add request body size limit in Nginx/FastAPI |
| Output token ceiling | Phase 6 max_tokens cap; verify enforcement |
| Structured audit log | Phase 7 audit events; verify completeness |
| Dependency pinning | Pin vLLM, Transformers, CUDA image, and model revision in `versions.env` |
| Database backups | pg_dump cron job or managed PostgreSQL with automated backups |
| No raw prompt logging | Test with log grep; confirm no prompt content in output |

### 8.9 TLS Termination

```nginx
# infra/docker/nginx.conf
server {
    listen 443 ssl;
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    location / {
        proxy_pass http://api-gateway:9000;
        proxy_http_version 1.1;
        proxy_set_header Connection '';   # needed for SSE streaming
        proxy_buffering off;              # critical for streaming
        proxy_cache off;
        proxy_read_timeout 120s;
    }
}
```

`proxy_buffering off` is critical to ensure SSE streaming reaches the client without Nginx buffering.

### 8.10 Failure Mode Verification (Spec Section 23)

Run explicit tests for each failure mode:

| Failure | Test | Expected Behaviour |
|---------|------|--------------------|
| vLLM not ready | Stop vLLM; send request | 503 with `vllm_not_ready` |
| GPU OOM | Very large batch or prompt | 503; request marked failed; alert logged |
| Adapter unavailable | Request unavailable adapter | Explicit 503/404; no silent base-model fallback |
| Object storage failure | Kill MinIO; trigger adapter download | Download fails; adapter → FAILED; existing active adapters continue |
| PostgreSQL unavailable | Stop postgres; send request | 503; fail closed for auth; no invented tenant state |
| Redis unavailable | Stop Redis; send request | Soft limits fail open with warning; hard limits fail closed |
| Client disconnect | Close connection mid-stream | Stream cancelled; usage record saved with `cancelled=true` |
| Queue saturation | Send 60+ concurrent requests | 503 with Retry-After; accepted requests unaffected |

### 8.11 Integration Test Suite

`tests/integration/`:

```
test_auth.py
  - valid key: 200
  - invalid key: 401
  - revoked key: 401
  - expired key: 401
  - cross-tenant adapter: 403

test_inference.py
  - streaming chat: SSE events received
  - non-streaming chat: complete response
  - context too long: 400
  - max_tokens respected

test_adapters.py
  - register adapter: 201
  - rank too large: 422
  - adapter lifecycle to ACTIVE
  - cross-tenant adapter use: 403

test_rate_limits.py
  - RPM exceeded: 429
  - concurrent limit: 429
  - global queue: 503

test_usage.py
  - usage record created after request
  - GET /v1/usage tenant-scoped
  - cross-tenant query blocked

test_health.py
  - /health returns 200 when vLLM is healthy
  - /health returns 503 when vLLM is unreachable
```

### 8.12 Minimal Admin UI

For M9 beta, a minimal UI can be a simple FastAPI-served HTML page or a separate React single-page app. Minimum features:

- View organizations and their API key count.
- Create and revoke API keys.
- View registered adapters and their status.
- View recent usage summary (requests, tokens, errors).
- View current worker health.

This can be served at `/admin` behind a separate admin authentication check.

### 8.13 Dependency and Version Pinning

`infra/docker/versions.env`:

```env
VLLM_IMAGE=vllm/vllm-openai:v0.5.4
CUDA_IMAGE=nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04
POSTGRES_IMAGE=postgres:16.3-alpine
REDIS_IMAGE=redis:7.2-alpine
PROMETHEUS_IMAGE=prom/prometheus:v2.52.0
GRAFANA_IMAGE=grafana/grafana:10.4.2
QWEN3_4B_REVISION=<git-sha-of-pinned-model-revision>
```

All Docker Compose `image:` references must use these pinned versions, not `latest`.

### 8.14 PostgreSQL Backup

For beta, at minimum configure a daily `pg_dump` cron:

```bash
# scripts/backup_postgres.sh
pg_dump -h $POSTGRES_HOST -U inferra inferra | gzip > /backups/inferra_$(date +%Y%m%d).sql.gz
# Upload to S3 with retention policy
aws s3 cp /backups/inferra_$(date +%Y%m%d).sql.gz s3://inferra-backups/postgres/
```

---

## Part C: V1 Success Criteria Verification

Before inviting the beta cohort, verify all criteria from spec section 34:

| Area | Success Criterion | Verified? |
|------|-------------------|-----------|
| Functionality | Reliable streaming chat completions through platform API | [ ] |
| Isolation | Tenant keys cannot access another tenant's private adapter | [ ] |
| Customization | ≥2 LoRA aliases sharing one Qwen3-4B deployment | [ ] |
| Observability | Every request trace includes queue/TTFT/total latency and tokens | [ ] |
| Capacity | Benchmarked concurrency envelope for 4K and 8K context workloads | [ ] |
| Reliability | Graceful overload; no unbounded queue/OOM loop | [ ] |
| Operations | GPU/KV/request metrics visible and actionable in Grafana | [ ] |
| Extensibility | Model/deployment/worker abstractions allow future second GPU without client API changes | [ ] |

---

## V2 Decision Triggers (Spec Section 27)

After the capacity report is written, evaluate:

| Observed Bottleneck | V2 Investment |
|--------------------|---------------|
| GPU consistently saturated at acceptable demand | Add second worker + simple scheduler |
| Customers need guaranteed latency | Priority queues, admission classes, SLOs |
| Hundreds of adapters / high load latency | Adapter cache manager, CPU cache, eviction |
| Long contexts dominate requests | FP8 KV cache, larger GPU tier |
| Customers request custom training | Separate LoRA fine-tuning job service |
| Different tasks need different models | Model catalog and capability-based routing |
| Reliability requires auto-replacement of GPU nodes | Kubernetes |
| Demand varies strongly by time | Autoscaling and warm-pool strategy |

---

## Final Beta Launch Checklist

**Benchmarking (M8)**
- [ ] Stage 1: Single-request baseline complete — TTFT, total latency, tokens/s recorded.
- [ ] Stage 2: Concurrency sweep 2→4→8→16 complete — all metrics captured per cell.
- [ ] Stage 3: Context sweep 4K vs 8K complete — VRAM delta and KV saturation recorded.
- [ ] Stage 4: LoRA mix (50/50 base vs adapter at c=4, c=8) complete.
- [ ] Stage 5: Shared-prefix workload complete — prefix_cache_hit_ratio recorded.
- [ ] Stage 6: FP8 KV experiment run — comparison table filled in; V2 decision made.
- [ ] Stage 7: Overload test complete — 429 pattern verified; no OOM or unbounded queue.
- [ ] Capacity report (`docs/architecture/v1-capacity-report.md`) written with all numbers.

**Beta Hardening (M9 — Spec Section 30)**
- [ ] TLS enabled — Nginx terminating external traffic (Let's Encrypt or self-signed for private beta).
- [ ] No plaintext traffic on any external interface.
- [ ] API key rotation tested end-to-end (`POST /v1/api-keys` → `DELETE /v1/api-keys/{id}`).
- [ ] All secrets via env/secret store — nothing sensitive committed to source.
- [ ] Dependency versions pinned: vLLM, CUDA image, Qwen3-4B model revision, all Docker images.
- [ ] `requirements.txt` pinned to exact versions (not `>=` ranges) before beta tag.
- [ ] PostgreSQL backup cron scheduled — daily `pg_dump` to S3 confirmed working.
- [ ] Adapter artifact validation tested with adversarial inputs (wrong rank, corrupt file).
- [ ] No raw prompt content in application logs — verified with `grep` on log output.
- [ ] Structured audit log verified for: key creation, key revocation, adapter lifecycle events.
- [ ] Input size limits confirmed at both FastAPI level and Nginx `client_max_body_size`.
- [ ] Output token ceiling confirmed — `max_tokens` cap enforced even if client sends higher.
- [ ] Tenant isolation audit — Tenant A cannot access Tenant B private adapter (tested in integration suite).
- [ ] All failure modes from spec section 23 tested and verified (see section 8.10 above).
- [ ] Integration test suite passes with 0 failures against real vLLM stack.

**Operations**
- [x] Admin UI accessible at `/admin` with admin key.
- [x] Grafana dashboard operational — live data requires GPU integration.
- [ ] GPU/VRAM/KV-cache panels showing real data (not mock).
- [ ] OpenTelemetry spans visible end-to-end (gateway → vLLM boundary).

**V1 Criteria**
- [ ] V1 success criteria table (section above) fully verified.
- [ ] V2 decision triggers evaluated and documented in the V2 decision log.
- [ ] Beta cohort briefed on known limitations (context ceiling, rate limits, LoRA constraints).

---

## Post-Implementation Documentation

This section records the as-built state of Phase 8 as of 2026-08-29. All benchmark scripts and hardening files are complete. Actual GPU benchmark numbers require the real L4 to be running with Stage G flags.

### Implementation Log

```
Date completed (partial): 2026-08-29
Implemented by: Cursor Agent
Status: CODE-COMPLETE — all benchmark scripts, hardening config, and integration tests
        created; actual benchmark numbers require real L4 GPU after Phase 1 Stage G;
        beta cohort not yet launched.
Git commit / tag: commit after full benchmark run on real GPU
Branch: main
```

### What Was Built vs. What Remains

```
BUILT (2026-08-29):
  [x] scripts/benchmark/baseline.py          — multi-profile baseline (256/512/1K/2K tokens)
                                               + streaming TTFT measurement (updated 2026-08-29)
  [x] scripts/benchmark/concurrency.py       — async concurrent sweep, stops at error rate > 2%
  [x] scripts/benchmark/context_sweep.py     — 2K/4K/8K context sweep at c=1,4,8
  [x] scripts/benchmark/lora_mix.py          — base vs adapter latency comparison
  [x] scripts/benchmark/prefix_cache.py      — cold vs warm TTFT measurement (8 user suffixes)
  [x] scripts/benchmark/overload.py          — RPM burst + concurrent limit burst tests
  [x] scripts/benchmark/report.py            — reads all JSON outputs, writes capacity-report.md
  [x] scripts/runpod/05_validate_streaming.sh — TTFT-measuring streaming validation (NEW)
  [x] scripts/runpod/06_run_all_benchmarks.sh — pod-side orchestrator for all 7 stages (NEW)
  [x] tests/load/locustfile.py               — 4-task Locust suite (short/medium/stream/long)
                                               with streaming SSE validation + --api-key flag
  [x] tests/integration/test_api.py          — integration tests (health, stream, auth, usage)
  [x] infra/docker/nginx.conf                — TLS termination + proxy_buffering off for SSE
  [x] infra/docker/versions.env              — all image versions pinned (updated 2026-08-29)
  [x] requirements.txt                       — exact versions pinned for beta tag (2026-08-29)
  [x] docs/runbooks/beta-checklist.md        — 13-section readiness checklist
  [x] scripts/seed_real_worker.py            — seeds real RunPod worker/deployment into DB
  [x] docs/architecture/v1-capacity-report.md — template with all required benchmark cells
  [x] apps/api/routes/admin_ui.py            — full admin UI at /admin (orgs/adapters/workers/usage)
  [x] scripts/backup_postgres.sh             — daily pg_dump to S3

PENDING (requires real GPU + Stage G):
  [ ] Actual benchmark numbers — run 06_run_all_benchmarks.sh after Stage G
  [ ] v1-capacity-report.md populated with real numbers
  [ ] FP8 KV cache experiment (Stage 6) — optional comparison, see plan §8.5 Stage 6
  [ ] Beta cohort launch — pending capacity report review
  [ ] Qwen3-4B model revision SHA in versions.env
```

### Benchmark Results Summary

```
STATUS: PENDING — benchmarks cannot run without the NVIDIA L4 GPU and Qwen3-4B model.

Baseline.py purpose: single-request TTFT + tokens/s measurement.
Expected mock-vLLM numbers (for reference only, not real GPU numbers):
  TTFT (mock): ~50ms (MOCK_TTFT_DELAY_MS)
  Decode: ~640ms (32 tokens × 20ms/token)
  Total: ~690ms

Real benchmark results to be filled in after RunPod L4 is provisioned.
See plans/no-gpu_build_order (section: "Data Plane Integration — When the L4 Arrives").
```

**4K Context — TTFT at Increasing Concurrency**

| Concurrency | TTFT P50 (ms) | TTFT P95 (ms) | TTFT P99 (ms) | Tokens/s (agg) | GPU Util % | VRAM % | Error Rate |
|-------------|--------------|--------------|--------------|---------------|-----------|--------|-----------|
| 1 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 2 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 4 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 8 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 16 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

**8K Context — TTFT at Increasing Concurrency**

| Concurrency | TTFT P50 (ms) | TTFT P95 (ms) | Total P95 (ms) | KV-cache % at peak |
|-------------|--------------|--------------|---------------|-------------------|
| 1 | PENDING | PENDING | PENDING | PENDING |
| 4 | PENDING | PENDING | PENDING | PENDING |
| 8 | PENDING | PENDING | PENDING | PENDING |

### Security Hardening — As-Built State

| Requirement | Status | Notes |
|-------------|--------|-------|
| TLS config template (Nginx) | CREATED | infra/docker/nginx.conf — template, not yet live |
| All secrets via env vars | YES | docker-compose.yml uses environment section only |
| No secrets committed to source | YES | .env.example only; .env in .gitignore |
| Dependency versions pinned | PARTIAL | requirements.txt uses >= ranges; versions.env pins Docker images |
| PostgreSQL backup | NOT YET | add pg_dump cron before beta |
| Input size limits at Nginx | TEMPLATE ONLY | nginx.conf has client_max_body_size 1m; not deployed |
| No raw prompt logging | YES | confirmed by code review of middleware/logging.py |
| Tenant ownership checks | YES | resolver.py and all CRUD routes filter by organization_id |
| Artifact validation before LoRA load | YES | validate_adapter_artifact() checks rank + config file |
| Structured audit log | YES | JSON log lines for key/adapter events (logger.info) |

### Failure Mode Implementation Status

| Failure | Expected Behaviour | Implemented? | Implementation Location |
|---------|-------------------|--------------|------------------------|
| vLLM not ready | 503 | YES | health.py checks vLLM; deps chain blocks startup |
| GPU OOM | 503, request marked failed | PARTIAL | vLLM error surfaced as 503; no specific OOM signal from mock |
| Adapter unavailable | Explicit 503, no silent fallback | YES | resolver.py raises 503 if adapter not in LOADED/ACTIVE state |
| Object storage failure | Adapter → FAILED | YES | registry.py catches botocore exceptions → sets status=failed |
| PostgreSQL unavailable | 503 fail closed | YES | SQLAlchemy exceptions propagate as 500/503 |
| Redis unavailable (soft) | Fail open + warning | YES | rate_limiter.py try/except around Redis calls |
| Redis unavailable (hard/quota) | Fail closed + 503 | YES | check_daily_quota returns False on exception |
| Client disconnect | cancelled record + usage saved | YES | CancelledError caught; background task still fires |
| Queue saturated | 503, accepted unaffected | YES | check_global_queue raises 503; queue decremented in finally |

### Integration Test — As-Built State

```
Test file: tests/integration/test_api.py
Tests written:
  - test_health_check           — GET /health returns 200
  - test_streaming_chat         — POST /v1/chat/completions with stream=True
  - test_non_streaming_chat     — POST /v1/chat/completions with stream=False
  - test_usage_recording        — usage record created after request
  - test_invalid_auth           — missing/invalid key returns 401
  - test_admin_key_rejected      — admin key on inference endpoint returns 403

Run status: NOT YET EXECUTED against live stack
  (requires: docker compose up + seed_dev_data.py to create test keys)
Environment variable needed: INFERRA_BASE_URL=http://localhost:9100
```

### V1 Success Criteria — Current Status

| Area | Success Criterion | Status |
|------|-------------------|--------|
| Functionality | Reliable streaming chat completions through platform API | IMPLEMENTED (mock) — test against real vLLM |
| Isolation | Tenant keys cannot access another tenant's private adapter | IMPLEMENTED — resolver.py + CRUD checks |
| Customization | ≥2 LoRA aliases sharing one Qwen3-4B deployment | IMPLEMENTED — alias table + registry ready; needs real adapters |
| Observability | Every request trace includes queue/TTFT/total latency and tokens | IMPLEMENTED — UsageMetric row per request |
| Capacity | Benchmarked concurrency envelope for 4K and 8K context | PENDING — no GPU yet |
| Reliability | Graceful overload; no unbounded queue/OOM loop | IMPLEMENTED — admission control confirmed |
| Operations | GPU/KV/request metrics visible in Grafana | PARTIAL — request metrics visible; GPU pending |
| Extensibility | Worker abstraction allows future second GPU without API changes | IMPLEMENTED — Worker + Deployment models decouple client from GPU |

### V2 Decision Log

```
To be filled in after benchmark results are collected with real L4 GPU.
Decisions should be driven by: capacity report, beta user feedback, observed failure modes.
```

### Beta Cohort Notes

```
Beta start date:                PENDING — requires RunPod L4 + real model
Prerequisite steps:             See /Users/swa/.cursor/plans/no-gpu_build_order_*.plan.md
                                "Data Plane Integration — When the L4 Arrives" (steps A–J)
On-call runbook location:       docs/runbooks/beta-checklist.md
Known limitations to communicate to beta users:
  - Context window: 8192 tokens maximum (V1 limit)
  - Rate limits: 60 RPM, 5 concurrent requests per API key
  - LoRA adapters: max rank 16, up to 4 loaded simultaneously
  - No SLA guarantee during beta
```

### Deviations from Plan

```
1. Full benchmark suite (all 6 scripts + report.py) created in a single implementation pass.
   Reason: More efficient to define the full data contract now; runs require the real L4.
   Resolution: Execute with real vLLM after Phase 1 Stage G; fill numbers into capacity report.

2. baseline.py extended to run multiple prompt profiles (short/medium/long/upper_v1_context)
   + a streaming TTFT measurement (not just single-request as originally scoped).
   Reason: Structured output allows report.py to populate all cells in the capacity report.
   Impact: Covers all spec §24.1 profiles in one script invocation.

3. Admin UI implemented as apps/api/routes/admin_ui.py (FastAPI + HTML, no React).
   Reason: Reduces scope; beta operators can use curl or the admin panel.
   Trade-off: No visual form inputs; actions (create/revoke key) still require API calls.

4. 05_validate_streaming.sh added (not in original plan scope).
   Reason: Stage F streaming script was truncated in an earlier session; a proper
   Python-based TTFT validator was needed to formally close Stage F.

5. 06_run_all_benchmarks.sh pod-side orchestrator added (not in original plan).
   Reason: Gives a single entry-point for the full benchmark suite; prevents skipping stages.

6. Locust locustfile.py enhanced with 4 tasks including streaming SSE validation.
   Original plan only had 2 tasks (short_chat, medium_chat, no streaming).
   Reason: Streaming path must be exercised under load; added streaming_chat + long_prompt tasks.

7. FP8 KV cache experiment kept as Stage 6 (V1 benchmark, not V2 deferral).
   Note: vLLM 0.28.0 must be checked for --kv-cache-dtype fp8 support.
   If unsupported, document as "version constraint — revisit on vLLM upgrade" and close.

8. requirements.txt pinned to exact versions (2026-08-29).
   Previously used >= ranges; pinned for beta tag reproducibility.
```

### Issues Encountered

```
None (no GPU-dependent code executed yet).
```

### Architecture Decisions Made

```
Decision 1:
  Context: Admin UI — web panel vs. REST API + seed script for V1.
  Choice made: REST API + seed_dev_data.py is sufficient for V1.
  Reason: Reduces scope; single-tenant dev environment needs only one admin.
  Trade-off: No visual interface; operators use curl / the admin API directly.
             Build a minimal Jinja2 panel in V2 if beta users need it.
```

---

## What Comes After Phase 8

Phase 8 closes V1. V2 decisions are driven exclusively by the capacity report and observed user feedback. No V2 scope is pre-committed. The governing principle from the spec remains:

> **Measure → Optimize → Collect Feedback → Expand**
