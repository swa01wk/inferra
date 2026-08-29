from prometheus_client import Counter, Gauge, Histogram

# ── Request counters ──────────────────────────────────────────────────────────

inference_requests_total = Counter(
    "inference_requests_total",
    "Total inference requests",
    ["status", "tenant_id", "logical_model"],
)

inference_errors_total = Counter(
    "inference_errors_total",
    "Inference errors by error code",
    ["error_code", "tenant_id"],
)

# ── Latency histograms ────────────────────────────────────────────────────────

ttft_seconds = Histogram(
    "inference_ttft_seconds",
    "Time to first token in seconds",
    ["logical_model"],
    buckets=[0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0],
)

total_latency_seconds = Histogram(
    "inference_total_latency_seconds",
    "Total request latency in seconds",
    ["logical_model"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# ── Throughput ────────────────────────────────────────────────────────────────

output_tokens_per_second = Histogram(
    "inference_output_tokens_per_second",
    "Output token generation rate (tokens/s per request)",
    ["logical_model"],
    buckets=[1, 5, 10, 20, 50, 100, 200],
)

# ── Token counters ────────────────────────────────────────────────────────────

prompt_tokens_total = Counter(
    "inference_prompt_tokens_total",
    "Total prompt tokens processed",
    ["tenant_id"],
)

completion_tokens_total = Counter(
    "inference_completion_tokens_total",
    "Total completion tokens generated",
    ["tenant_id"],
)

# ── Concurrency / queue gauges ────────────────────────────────────────────────

active_requests_gauge = Gauge(
    "inference_active_requests",
    "Currently active inference requests per tenant",
    ["tenant_id"],
)

global_queue_depth = Gauge(
    "inference_global_queue_depth",
    "Global active inference count across all tenants",
)

# ── Rate limit events ─────────────────────────────────────────────────────────

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "Rate limit rejections",
    ["reason", "tenant_id"],
    # reason: rpm | concurrent | quota | queue_depth | input_token_ceiling
)

# ── Adapter metrics ───────────────────────────────────────────────────────────

adapter_load_total = Counter(
    "adapter_load_total",
    "Adapter load attempts",
    ["adapter_id", "status"],  # status: success | failed
)

adapter_load_latency_seconds = Histogram(
    "adapter_load_latency_seconds",
    "Time to load a LoRA adapter into vLLM",
    ["adapter_id"],
    buckets=[0.5, 1.0, 2.0, 5.0, 15.0, 30.0],
)

active_adapters_loaded = Gauge(
    "active_adapters_loaded",
    "Number of LoRA adapters currently loaded in vLLM",
)
