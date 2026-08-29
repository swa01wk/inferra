# Observability

Inferra ships with a full observability stack: Prometheus metrics, pre-built Grafana dashboards, and optional OpenTelemetry distributed tracing.

---

## Architecture

```
API Gateway (:9100)
    │  /metrics endpoint (Prometheus text format)
    ▼
Prometheus (:9090)
    │  scrapes every 15s
    ▼
Grafana (:3000)
    │  pre-built dashboards provisioned from infra/grafana/
    │
    ├── inferra-v1.json    ← Inferra request / latency / token dashboard
    └── datasource.yml     ← Prometheus datasource auto-configured
```

When a real GPU is connected, Prometheus also scrapes:
- **vLLM native metrics** from `http://host.docker.internal:8001/metrics`
- **NVIDIA GPU metrics** via nvidia-smi exporter (if configured)

---

## Prometheus Metrics Reference

All metrics are exposed at `GET /metrics` (Prometheus text format). No authentication required.

### Request Counters

#### `inference_requests_total`
**Type:** Counter  
**Labels:** `status`, `tenant_id`, `logical_model`

Total number of inference requests by outcome.

```promql
# Request rate by status
rate(inference_requests_total[1m])

# Failed requests by tenant
rate(inference_requests_total{status="failed"}[5m])
```

#### `inference_errors_total`
**Type:** Counter  
**Labels:** `error_code`, `tenant_id`

Errors grouped by error code (first 64 chars of exception message).

```promql
increase(inference_errors_total[1h])
```

### Latency Histograms

#### `inference_ttft_seconds`
**Type:** Histogram  
**Labels:** `logical_model`  
**Buckets:** 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0 seconds

Time-to-first-token for streaming requests. Measured from when the request was forwarded to vLLM until the first content chunk arrived.

```promql
# P50 TTFT
histogram_quantile(0.5, rate(inference_ttft_seconds_bucket[5m]))

# P95 TTFT
histogram_quantile(0.95, rate(inference_ttft_seconds_bucket[5m]))

# P99 TTFT
histogram_quantile(0.99, rate(inference_ttft_seconds_bucket[5m]))
```

#### `inference_total_latency_seconds`
**Type:** Histogram  
**Labels:** `logical_model`  
**Buckets:** 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0 seconds

End-to-end wall-clock latency from gateway receipt to last response byte.

```promql
histogram_quantile(0.95, rate(inference_total_latency_seconds_bucket[5m]))
```

### Throughput

#### `inference_output_tokens_per_second`
**Type:** Histogram  
**Labels:** `logical_model`  
**Buckets:** 1, 5, 10, 20, 50, 100, 200 tokens/s

Per-request decode throughput (`completion_tokens / decode_time_s`).

```promql
# Median token generation speed
histogram_quantile(0.5, rate(inference_output_tokens_per_second_bucket[5m]))
```

### Token Counters

#### `inference_prompt_tokens_total`
**Type:** Counter  
**Labels:** `tenant_id`

Cumulative prompt tokens consumed per organization.

```promql
increase(inference_prompt_tokens_total{tenant_id="<uuid>"}[24h])
```

#### `inference_completion_tokens_total`
**Type:** Counter  
**Labels:** `tenant_id`

Cumulative completion tokens generated per organization.

```promql
# Total token spend in last hour
increase(inference_prompt_tokens_total[1h]) + increase(inference_completion_tokens_total[1h])
```

### Concurrency / Queue Gauges

#### `inference_active_requests`
**Type:** Gauge  
**Labels:** `tenant_id`

Currently in-flight requests per tenant (point-in-time).

#### `inference_global_queue_depth`
**Type:** Gauge

System-wide count of requests actively being processed (in vLLM or awaiting first token). Mirrors the Redis `rl:queue_depth` value.

```promql
# Alert when queue approaches limit
inference_global_queue_depth > 40
```

### Rate Limit Events

#### `rate_limit_rejections_total`
**Type:** Counter  
**Labels:** `reason`, `tenant_id`

`reason` values: `input_token_ceiling` | `rpm` | `concurrent` | `quota` | `queue_depth`

```promql
# Rate limit rejection rate by reason
rate(rate_limit_rejections_total[5m])

# Top tenants being rate-limited
topk(5, increase(rate_limit_rejections_total[1h]))
```

### Adapter Metrics

#### `adapter_load_total`
**Type:** Counter  
**Labels:** `adapter_id`, `status` (`success` | `failed`)

Total adapter load attempts.

#### `adapter_load_latency_seconds`
**Type:** Histogram  
**Labels:** `adapter_id`  
**Buckets:** 0.5, 1.0, 2.0, 5.0, 15.0, 30.0 seconds

Time to load a LoRA adapter into vLLM.

#### `active_adapters_loaded`
**Type:** Gauge

Number of LoRA adapters currently in `loaded` or `active` state.

---

## Grafana Dashboards

### Accessing Grafana

- URL: `http://localhost:3000`
- Username: `admin`
- Password: `admin`

The **Inferra V1** dashboard is automatically provisioned from `infra/grafana/dashboards/inferra-v1.json`.

### Dashboard Panels

| Panel | Metric | Description |
|-------|--------|-------------|
| Request Rate | `inference_requests_total` | req/s by status |
| TTFT P50/P95/P99 | `inference_ttft_seconds` | Latency percentiles |
| Total Latency P95 | `inference_total_latency_seconds` | End-to-end P95 |
| Token Throughput | `inference_output_tokens_per_second` | Decode tokens/s |
| Active Queue Depth | `inference_global_queue_depth` | System saturation gauge |
| Rate Limit Rejections | `rate_limit_rejections_total` | By rejection type |
| Prompt / Completion Tokens | `inference_*_tokens_total` | Token consumption rate |
| Active Adapters | `active_adapters_loaded` | Currently loaded LoRA count |

### Configuring Alerts

To add an alert for high TTFT in Grafana:

1. Edit the "TTFT P95" panel
2. Click "Alert" tab
3. Set condition: `WHEN last() OF query(A, 5m, now) IS ABOVE 2` (2 seconds)
4. Set notification channel (email, Slack, PagerDuty)

---

## Prometheus Configuration

**Config file:** `infra/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'inferra-gateway'
    static_configs:
      - targets: ['api-gateway:9000']  # internal Docker network

  # Uncomment when real GPU is connected:
  # - job_name: 'vllm'
  #   static_configs:
  #     - targets: ['host.docker.internal:8001']
```

Default retention: 15 days (`--storage.tsdb.retention.time=15d`).

---

## OpenTelemetry Distributed Tracing

Tracing is **disabled by default**. Enable it to get gateway → vLLM span correlation in Jaeger, Grafana Tempo, or any OTLP backend.

### Enabling Tracing

Set environment variables in `docker-compose.yml` or `.env`:

```env
OTEL_ENABLED=true
OTEL_ENDPOINT=http://localhost:4317
```

Or set in `docker-compose.yml`:
```yaml
api-gateway:
  environment:
    OTEL_ENABLED: "true"
    OTEL_ENDPOINT: "http://tempo:4317"
```

### What Gets Traced

When `OTEL_ENABLED=true`, every FastAPI request gets a root span, and every outbound httpx call to vLLM gets a child span:

```
Trace: POST /v1/chat/completions
  ├── Span: auth.validate_key (DB lookup)
  ├── Span: admission.check (Redis calls)
  ├── Span: routing.resolve_target (DB lookup)
  └── Span: vllm.chat_completions_stream (HTTP to vLLM)
       ├── duration: full stream time
       └── tags: model, adapter_id, status
```

### Adding Grafana Tempo

Add to `docker-compose.yml`:
```yaml
tempo:
  image: grafana/tempo:2.4.0
  command: ["-config.file=/etc/tempo.yaml"]
  volumes:
    - ./infra/tempo/tempo.yaml:/etc/tempo.yaml
  ports:
    - "4317:4317"   # OTLP gRPC
    - "3200:3200"   # Tempo HTTP API
```

Then add a Tempo data source in Grafana (`http://tempo:3200`) to correlate trace IDs with log lines.

---

## Structured Logging

The gateway emits structured JSON logs for key events:

### API Key Created
```json
{"event": "api_key_created", "api_key_id": "...", "organization_id": "..."}
```

### API Key Revoked
```json
{"event": "api_key_revoked", "api_key_id": "...", "organization_id": "..."}
```

### Adapter Status Changed
```json
{"event": "adapter_status_changed", "adapter_id": "...", "to_status": "active"}
```

### Request Logging Middleware
Every request logs:
- Method, path, status code, response time
- `X-Request-ID` (UUID) for correlation

Log level is controlled by `LOG_LEVEL` env var (default `INFO`). Set to `DEBUG` for verbose output including all Redis operations.

---

## Correlating Logs with Traces

The `X-Request-ID` header (a UUID) is injected by `RequestLoggingMiddleware` and stored in the `requests` table as the primary key. To correlate:

1. Find the `request_id` in usage data or DB
2. Search Prometheus for the same UUID in label dimensions
3. Search trace backend for the same UUID as a span tag

---

## Key Prometheus Queries for Operations

```promql
# Current request rate
rate(inference_requests_total[1m])

# Error rate as a percentage
rate(inference_requests_total{status="failed"}[5m])
  / rate(inference_requests_total[5m]) * 100

# TTFT SLO: % of requests under 500ms
rate(inference_ttft_seconds_bucket{le="0.5"}[5m])
  / rate(inference_ttft_seconds_count[5m]) * 100

# Daily token consumption by tenant
increase(inference_prompt_tokens_total[24h])
  + increase(inference_completion_tokens_total[24h])

# Queue saturation (alert if > 80% of limit)
inference_global_queue_depth / 50 * 100 > 80
```
