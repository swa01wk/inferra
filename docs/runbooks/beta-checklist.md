# Beta Launch Checklist

> Derived from spec section 30 (Security and Operational Hardening) and spec section 34 (Success Criteria).
> Complete every item before inviting the first external beta user.
> Mark `[x]` when verified with evidence (test output, log grep, config file path).

---

## 1. Benchmarking Complete

- [ ] Stage 1 (single-request baseline) — TTFT, total latency, tokens/s recorded for all prompt profiles
- [ ] Stage 2 (concurrency sweep 2→4→8→16) — all metrics captured; error rate < 2% at each level
- [ ] Stage 3 (context sweep 4K vs 8K) — VRAM delta and KV-cache saturation documented
- [ ] Stage 4 (LoRA mix) — base vs adapter latency delta recorded
- [ ] Stage 5 (shared-prefix) — prefix_cache_hit_ratio recorded and confirms prefix reuse
- [ ] Stage 6 (FP8 KV experiment) — comparison table filled in; V2 decision logged
- [ ] Stage 7 (overload / admission control) — 429 behaviour verified; no OOM or queue blowup
- [ ] `docs/architecture/v1-capacity-report.md` fully populated with real numbers

---

## 2. TLS and Network Security

- [ ] Nginx reverse proxy deployed in front of `api-gateway` container
- [ ] TLS certificate installed (Let's Encrypt for public endpoint, self-signed acceptable for closed beta)
- [ ] `proxy_buffering off` confirmed in nginx.conf — required for SSE streaming
- [ ] HTTP → HTTPS redirect confirmed (port 80 redirects to 443)
- [ ] No HTTP plaintext traffic reachable from outside the host network
- [ ] `client_max_body_size` set in nginx.conf to limit request body size

---

## 3. Secrets and Credentials

- [ ] No plaintext API keys in PostgreSQL — only `key_hash` stored (verified by DB inspection)
- [ ] Secret value returned exactly once at key creation — tested by creating a key and verifying it cannot be retrieved again
- [ ] `.env` file is in `.gitignore` — confirmed not tracked by git
- [ ] No credentials in `docker-compose.yml` or source code — all via environment variables
- [ ] `ADMIN_SECRET` changed from `dev-admin-secret-change-me` default before beta
- [ ] MinIO credentials changed from `minioadmin` / `minioadmin` default before beta
- [ ] PostgreSQL password changed from `inferra` default before beta

---

## 4. API Key Lifecycle

- [ ] Key creation (`POST /v1/api-keys`) returns secret exactly once — confirmed
- [ ] Immediate revocation (`DELETE /v1/api-keys/{key_id}`) — revoked key returns 401 within 1 request
- [ ] Expired key returns 401 (set `expires_at` to past; confirm rejection)
- [ ] Admin key rejected on inference endpoints — returns 403
- [ ] Inference key rejected on admin endpoints — returns 403
- [ ] Cross-tenant adapter access rejected — Tenant A's key returns 403 on Tenant B's private adapter

---

## 5. Dependency and Version Pinning

- [ ] vLLM image version pinned in `infra/docker/versions.env` (not `latest`)
- [ ] CUDA image tag pinned (not `latest`)
- [ ] Qwen3-4B model revision (HuggingFace git SHA) recorded in `versions.env`
- [ ] All Docker Compose `image:` references use pinned tags — no `latest` anywhere
- [ ] `requirements.txt` pinned to exact versions (not `>=` ranges) for the beta tag
- [ ] vLLM launch flags and version recorded in `plans/phase-1-infrastructure-and-runtime.md` post-impl section

---

## 6. Data Durability

- [ ] PostgreSQL backup cron scheduled — daily `pg_dump` to S3/off-instance storage
- [ ] Backup restoration tested (restore to a test DB and verify table counts)
- [ ] MinIO adapter artifacts confirmed persisted outside ephemeral container storage
- [ ] `/workspace` volume confirmed persistent across Pod restarts (RunPod network volume)
- [ ] Postgres volume confirmed persistent across `docker compose down` (named Docker volume)

---

## 7. Logging and Audit

- [ ] No raw prompt content in application logs — verified: `grep -r "content" logs/` shows no user text
- [ ] Structured audit events present in logs for: key creation, key revocation, adapter state transitions
- [ ] `request_id` present in every log line for a traced request
- [ ] Adapter storage paths not exposed in any API response — confirmed by reviewing adapter endpoint responses

---

## 8. Input / Output Controls

- [ ] Input token ceiling enforced at gateway — request with tokens > `max_input_tokens` returns 400
- [ ] Output token ceiling enforced — request with `max_tokens` > `max_output_tokens` policy is capped
- [ ] Context length limit enforced — request with prompt + max_tokens > 8192 is rejected with clear error
- [ ] Nginx `client_max_body_size` blocks oversized request bodies before they hit the gateway

---

## 9. Failure Mode Verification

Run each test manually or via integration suite and mark verified:

- [ ] **vLLM not ready** — stop vLLM, send request → `503` with `vllm_not_ready`, `/health` shows `degraded`
- [ ] **GPU OOM** — trigger with very large prompt/concurrency → `503`, request marked `failed`, alert logged
- [ ] **Adapter unavailable** — request adapter in `REGISTERED` state → explicit `503`, no silent base-model fallback
- [ ] **Object storage failure** — kill MinIO, trigger adapter download → adapter moves to `FAILED`, existing active adapters unaffected
- [ ] **PostgreSQL unavailable** — stop Postgres, send request → `503` fail-closed; no invented tenant state
- [ ] **Redis unavailable (soft limits)** — stop Redis, send request → fail-open with warning logged
- [ ] **Redis unavailable (hard quota)** — stop Redis, trigger quota check → fail-closed, request rejected
- [ ] **Client disconnect** — close connection mid-stream → `cancelled=True` in usage record, partial usage saved
- [ ] **Queue saturation** — send 60+ concurrent requests → `503` returned for excess; accepted requests not degraded

---

## 10. Observability

- [ ] Grafana dashboard loads at `http://localhost:3000` (admin / admin)
- [ ] All panels show live data (not mock/empty) after integration with real vLLM
- [ ] `requests_per_minute`, `ttft_seconds`, `total_latency_seconds` histograms receiving data
- [ ] GPU utilization and VRAM panels showing real GPU metrics
- [ ] KV-cache utilization panel showing real vLLM data
- [ ] Queue depth visible under load
- [ ] OpenTelemetry spans visible (if OTel endpoint configured)
- [ ] Error rate panel fires correctly when inference errors are injected

---

## 11. Integration Tests

- [ ] `test_health` — passes against real vLLM stack
- [ ] `test_streaming_chat` — real tokens arrive incrementally
- [ ] `test_usage_endpoint` — real token counts in usage record (not mock numbers)
- [ ] `test_invalid_key` — 401 returned
- [ ] `test_admin_endpoint_rejects_inference_key` — 403 returned
- [ ] Full suite: `pytest tests/integration -v` → 0 failures, 0 errors

---

## 12. V1 Success Criteria (Spec Section 34)

| Area | Success Criterion | Verified? |
|------|-------------------|-----------|
| Functionality | Reliable streaming chat completions through platform API | [ ] |
| Isolation | Tenant keys cannot access another tenant's private adapter | [ ] |
| Customization | ≥2 LoRA aliases sharing one Qwen3-4B deployment | [ ] |
| Observability | Every request trace includes queue/TTFT/total latency and tokens | [ ] |
| Capacity | Benchmarked concurrency envelope for 4K and 8K context workloads | [ ] |
| Reliability | Graceful overload; no unbounded queue/OOM loop | [ ] |
| Operations | GPU/KV/request metrics visible and actionable in Grafana | [ ] |
| Extensibility | Worker abstraction allows future second GPU without client API changes | [ ] |

---

## 13. Beta Cohort Briefing

Before sharing access with the first external user, communicate:

- [ ] Context window: **8,192 tokens maximum** (prompt + completion combined)
- [ ] Rate limits: **60 RPM**, **5 concurrent requests** per API key
- [ ] LoRA adapters: max rank 16, up to 4 adapters loaded simultaneously
- [ ] No SLA guarantee during beta
- [ ] Pod may be restarted for maintenance — 5 min notice where possible
- [ ] On-call runbook location shared with internal operators: `docs/runbooks/runpod-poc-runbook.md`
- [ ] Feedback channel established (Slack / email / GitHub Issues)

---

## Sign-off

| Milestone | Owner | Date | Notes |
|-----------|-------|------|-------|
| Benchmarking complete (M8) | | | |
| Beta hardening complete (M9) | | | |
| First external user invited | | | |
