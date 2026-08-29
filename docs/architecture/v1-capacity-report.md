# V1 Safe Service Envelope — Qwen3-4B on NVIDIA L4 24 GB

**Platform:** Inferra V1 · RunPod NVIDIA L4 24 GB · vLLM 0.28.0 · Qwen/Qwen3-4B BF16  
**Status:** GPU CONFIRMED (Stage D) — benchmark numbers pending Phase 8 run after Stage G

> To populate all PENDING cells, run `scripts/runpod/06_run_all_benchmarks.sh` after Phase 1 Stage G,
> then `python scripts/benchmark/report.py` to auto-generate this document with real numbers.

---

## GPU and Memory at Startup

| Measurement | Value |
|-------------|-------|
| GPU model confirmed | **NVIDIA L4 24 GB** (confirmed 2026-08-29) |
| Total VRAM | **23,034 MiB** (~22.5 GB usable) |
| VRAM used at Stage D idle (4K ctx, BF16, util=0.85) | **19,112 MiB (83%)** |
| Weight allocation (Qwen3-4B BF16, estimated) | ~8,500 MiB |
| vLLM runtime / CUDA overhead (estimated) | ~1,200 MiB |
| KV-cache pool at Stage D (util=0.85) | ~9,412 MiB |
| VRAM free at Stage D idle | 3,922 MiB |
| VRAM used at Stage G idle (8K ctx, util=0.90) | PENDING — run `04_finalize_phase1.sh` |
| KV-cache pool at Stage G (util=0.90, estimated) | ~10,731 MiB |

Reference: `/workspace/benchmarks/gpu-after-v1-load.txt` (captured by `04_finalize_phase1.sh`).

---

## KV-Cache Capacity Reference (BF16, Qwen3-4B)

```
KV bytes/token = 36 layers × 8 KV heads × 128 head_dim × 2 (K+V) × 2 bytes = 147,456 bytes ≈ 144 KiB/token
```

| Context tokens | Approx BF16 KV per fully cached sequence |
|----------------|------------------------------------------|
| 2,048 | ~288 MiB |
| 4,096 | ~576 MiB |
| 8,192 | ~1.125 GiB |

---

## vLLM Launch Configuration Used

```
vllm version:              0.28.0 (pip-installed in /workspace/vllm-env/)
Qwen3-4B model revision:   PENDING (run: git -C /workspace/models/Qwen3-4B rev-parse HEAD)

Stage D flags (4K baseline — in use as of 2026-08-29):
  --dtype                  bfloat16
  --max-model-len          4096
  --gpu-memory-utilization 0.85
  --host 0.0.0.0 --port 8000

Stage G flags (V1 production — apply with 04_finalize_phase1.sh):
  --dtype                  bfloat16
  --max-model-len          8192
  --gpu-memory-utilization 0.90
  --enable-lora --max-lora-rank 16 --max-loras 4
  --enable-prefix-caching
  --host 0.0.0.0 --port 8000

Qwen3 note: disable thinking mode for all platform requests:
  chat_template_kwargs: {enable_thinking: false}
```

---

## Stage 1: Single-Request Latency Baseline

Run: `python scripts/benchmark/baseline.py`

| Prompt Tokens | Output Tokens | TTFT P50 (ms) | TTFT P95 (ms) | Total Latency (ms) | Tokens/s |
|--------------|---------------|--------------|--------------|-------------------|----------|
| 256 | 128 | PENDING | PENDING | PENDING | PENDING |
| 512 | 128 | PENDING | PENDING | PENDING | PENDING |
| 1024 | 256 | PENDING | PENDING | PENDING | PENDING |
| 2048 | 256 | PENDING | PENDING | PENDING | PENDING |

---

## Stage 2: Concurrency Sweep (4K context, BF16)

Run: `python scripts/benchmark/concurrency.py`

Stop criterion: error rate > 2% OR P99 TTFT > 10s.

| Concurrency | TTFT P50 (ms) | TTFT P95 (ms) | TTFT P99 (ms) | Total P95 (ms) | Tokens/s (agg) | GPU Util % | VRAM % | Error Rate |
|-------------|--------------|--------------|--------------|---------------|----------------|-----------|--------|-----------|
| 1 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 2 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 4 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 8 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 16 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 32 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

---

## Stage 3: Context Sweep (concurrency=4 and concurrency=8)

Run: `python scripts/benchmark/context_sweep.py`

| Context Tokens | Concurrency | TTFT P50 (ms) | TTFT P95 (ms) | Total P95 (ms) | KV-cache % at peak |
|---------------|-------------|--------------|--------------|---------------|-------------------|
| 2048 | 4 | PENDING | PENDING | PENDING | PENDING |
| 4096 | 4 | PENDING | PENDING | PENDING | PENDING |
| 8192 | 4 | PENDING | PENDING | PENDING | PENDING |
| 2048 | 8 | PENDING | PENDING | PENDING | PENDING |
| 4096 | 8 | PENDING | PENDING | PENDING | PENDING |
| 8192 | 8 | PENDING | PENDING | PENDING | PENDING |

---

## Stage 4: Base Model vs LoRA Mix (concurrency=4 and 8)

Run: `python scripts/benchmark/lora_mix.py`

| Mix | Concurrency | TTFT P95 (ms) | Adapter Load Latency (ms) | Notes |
|-----|-------------|--------------|--------------------------|-------|
| 100% base | 4 | PENDING | N/A | baseline |
| 50% base / 50% LoRA | 4 | PENDING | PENDING | |
| 100% LoRA | 4 | PENDING | PENDING | |
| 50% base / 50% LoRA | 8 | PENDING | PENDING | |

---

## Stage 5: Prefix Cache Effectiveness

Run: `python scripts/benchmark/prefix_cache.py`

| Shared Prefix Tokens | User Suffix Tokens | Prefix Cache Hit Rate | TTFT P95 without cache (ms) | TTFT P95 with cache (ms) | Improvement |
|---------------------|-------------------|----------------------|-----------------------------|--------------------------|-------------|
| 2048 | 128 | PENDING | PENDING | PENDING | PENDING |
| 2048 | 256 | PENDING | PENDING | PENDING | PENDING |

---

## Stage 6: FP8 KV Cache Experiment (V1 benchmark — not the shipped default)

> This is a V1 benchmark step to quantify the capacity gain from FP8 KV-cache.
> BF16 remains the V1 production default. FP8 is evaluated for V2 promotion if gain > 15%.
> See `plans/phase-8-benchmarking-and-beta.md §8.5 Stage 6` for the exact restart flags.

| Dimension | BF16 baseline | FP8 experiment |
|-----------|---------------|----------------|
| KV element width | 16-bit | 8-bit |
| VRAM at idle (after model load) | PENDING | PENDING |
| Max cacheable tokens (estimated) | PENDING | PENDING |
| TTFT P95 at c=4, 4K ctx | PENDING | PENDING |
| TTFT P95 at c=4, 8K ctx | PENDING | PENDING |
| Output tokens/s at c=4 | PENDING | PENDING |
| Quality delta | baseline | PENDING |
| V2 promotion decision | N/A | promote if gain > 15% |

**Note:** vLLM 0.28.0 must be verified for `--kv-cache-dtype fp8` support.
If unsupported, document as "version constraint — revisit on next vLLM upgrade."

---

## Stage 7: Overload and Admission Control Tests

Run: `python scripts/benchmark/overload.py`

| Test | Expected Result | Actual Result |
|------|----------------|---------------|
| RPM limit at 2× configured rate | 429 with Retry-After:1 | PENDING |
| Concurrent limit at 2× configured | 429 immediately | PENDING |
| Global queue saturated (50+ active) | 503 with Retry-After:5 | PENDING |
| No CUDA OOM under overload | ✓ model healthy | PENDING |
| Accepted request TTFT unaffected by 429 surge | TTFT stable | PENDING |

---

## Recommended Operating Points

> Fill in after Stage 2 concurrency sweep.

| Workload | Max Concurrency | Target TTFT P95 |
|----------|----------------|----------------|
| Interactive chat (fast TTFT) | PENDING | < 500 ms |
| Async/batch (relaxed TTFT) | PENDING | < 2 s |
| Maximum safe concurrency | PENDING | < 5 s |

---

## V2 Investment Triggers

Based on observed bottlenecks, these decisions should be made post-beta:

| Observed Bottleneck | Trigger Threshold | V2 Investment |
|--------------------|------------------|---------------|
| GPU saturation at acceptable demand | Utilization > 85% sustained | Add second worker + simple scheduler |
| Customers need guaranteed latency | P99 TTFT SLO complaints | Priority queues, SLOs |
| Hundreds of adapters / high load latency | > 20 adapters, adapter load > 30s | CPU warm cache, LRU eviction |
| Long contexts dominate traffic | > 40% requests > 4K tokens | FP8 KV-cache, larger GPU tier |
| Demand varies strongly by time | > 50% off-peak idle | Autoscaling and warm-pool strategy |

---

## Notes

- All pending rows above should be filled in from `scripts/benchmark/report.py` output.
- Record actual vLLM launch flags and version alongside these results.
- Keep this document under version control so V2 comparisons are accurate.
