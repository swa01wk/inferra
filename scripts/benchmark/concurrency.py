#!/usr/bin/env python3
"""
Concurrency sweep benchmark.

Sends N simultaneous requests to the gateway and measures TTFT, total latency,
tokens/s, and error rate at each concurrency level.

Usage:
    python scripts/benchmark/concurrency.py \\
        --url http://localhost:9100/v1/chat/completions \\
        --api-key inf_... \\
        --concurrency 1 2 4 8 16 \\
        --output /workspace/benchmarks/concurrency.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from typing import List

import httpx

MEDIUM_PROMPT = (
    "You are a helpful assistant. "
    "Explain the concept of attention mechanisms in transformer models "
    "in clear, accessible language. Cover self-attention, multi-head attention, "
    "and why they matter for language understanding."
)


@dataclass
class RequestResult:
    concurrency: int
    prompt_tokens: int
    completion_tokens: int
    ttft_ms: float | None
    total_ms: float
    tokens_per_second: float
    status_code: int
    error: str | None = None


async def single_request(
    client: httpx.AsyncClient,
    url: str,
    api_key: str | None,
    concurrency: int,
    max_tokens: int,
    stream: bool,
) -> RequestResult:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": "test-assistant",
        "messages": [{"role": "user", "content": MEDIUM_PROMPT}],
        "max_tokens": max_tokens,
        "stream": stream,
    }

    t_start = time.monotonic()
    ttft_ms: float | None = None
    prompt_tokens = 0
    completion_tokens = 0
    status_code = 0
    error: str | None = None

    try:
        if stream:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                status_code = resp.status_code
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if ttft_ms is None and line.startswith("data:") and "[DONE]" not in line:
                        ttft_ms = (time.monotonic() - t_start) * 1000
                    if line.startswith("data:") and "[DONE]" not in line:
                        try:
                            chunk = json.loads(line[5:].strip())
                            usage = chunk.get("usage") or {}
                            if usage.get("prompt_tokens"):
                                prompt_tokens = usage["prompt_tokens"]
                            if usage.get("completion_tokens"):
                                completion_tokens = usage["completion_tokens"]
                        except json.JSONDecodeError:
                            pass
        else:
            resp = await client.post(url, headers=headers, json=payload)
            status_code = resp.status_code
            resp.raise_for_status()
            body = resp.json()
            usage = body.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            ttft_ms = None  # non-streaming: no TTFT measurement

        total_ms = (time.monotonic() - t_start) * 1000
        tokens_per_second = (completion_tokens / (total_ms / 1000)) if total_ms > 0 and completion_tokens > 0 else 0.0

    except Exception as exc:
        total_ms = (time.monotonic() - t_start) * 1000
        error = str(exc)
        tokens_per_second = 0.0

    return RequestResult(
        concurrency=concurrency,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        tokens_per_second=tokens_per_second,
        status_code=status_code,
        error=error,
    )


async def run_at_concurrency(
    url: str,
    api_key: str | None,
    concurrency: int,
    requests_per_level: int,
    max_tokens: int,
    stream: bool,
) -> List[RequestResult]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [
            single_request(client, url, api_key, concurrency, max_tokens, stream)
            for _ in range(requests_per_level)
        ]
        return list(await asyncio.gather(*tasks))


def summarize(results: List[RequestResult], concurrency: int) -> dict:
    successes = [r for r in results if r.error is None and r.status_code < 400]
    errors = [r for r in results if r.error is not None or r.status_code >= 400]

    total_ms_vals = [r.total_ms for r in successes]
    ttft_vals = [r.ttft_ms for r in successes if r.ttft_ms is not None]
    tps_vals = [r.tokens_per_second for r in successes if r.tokens_per_second > 0]

    def pct(vals: list, p: float) -> float | None:
        if not vals:
            return None
        sorted_vals = sorted(vals)
        idx = int(len(sorted_vals) * p / 100)
        return round(sorted_vals[min(idx, len(sorted_vals) - 1)], 1)

    return {
        "concurrency": concurrency,
        "total_requests": len(results),
        "successful": len(successes),
        "errors": len(errors),
        "error_rate_pct": round(len(errors) / len(results) * 100, 1) if results else 0,
        "ttft_p50_ms": pct(ttft_vals, 50),
        "ttft_p95_ms": pct(ttft_vals, 95),
        "ttft_p99_ms": pct(ttft_vals, 99),
        "total_latency_p50_ms": pct(total_ms_vals, 50),
        "total_latency_p95_ms": pct(total_ms_vals, 95),
        "total_latency_p99_ms": pct(total_ms_vals, 99),
        "tokens_per_second_p50": pct(tps_vals, 50),
        "tokens_per_second_p95": pct(tps_vals, 95),
        "mean_tokens_per_second": round(statistics.mean(tps_vals), 1) if tps_vals else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9100/v1/chat/completions")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8, 16])
    parser.add_argument("--requests-per-level", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--stream", action="store_true", default=True)
    parser.add_argument("--output", default=None, help="Write JSON results to this file")
    args = parser.parse_args()

    all_summaries = []
    for c in args.concurrency:
        print(f"Running concurrency={c} ({args.requests_per_level} requests)…", flush=True)
        results = asyncio.run(
            run_at_concurrency(args.url, args.api_key, c, args.requests_per_level, args.max_tokens, args.stream)
        )
        summary = summarize(results, c)
        all_summaries.append(summary)
        print(
            f"  c={c:>2}  err={summary['error_rate_pct']}%  "
            f"TTFT_P95={summary['ttft_p95_ms']}ms  "
            f"lat_P95={summary['total_latency_p95_ms']}ms  "
            f"tok/s_P50={summary['tokens_per_second_p50']}"
        )

        # Stop if error rate > 2% — signal overload
        if summary["error_rate_pct"] > 2:
            print(f"  ⚠ Error rate {summary['error_rate_pct']}% > 2% — stopping sweep.")
            break

    output = {"benchmark": "concurrency_sweep", "results": all_summaries}
    print("\n" + json.dumps(output, indent=2))
    if args.output:
        import pathlib
        pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.output).write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
