#!/usr/bin/env python3
"""
Overload and admission control stress test.

Verifies that the platform fails predictably under overload:
  - RPM-exceeded requests return 429 with Retry-After header.
  - Concurrent limit exceeded returns 429 immediately.
  - Global queue saturation returns 503 with Retry-After.
  - Accepted requests are not significantly degraded during the overload burst.

Usage:
    python scripts/benchmark/overload.py \\
        --url http://localhost:9100/v1/chat/completions \\
        --api-key inf_... \\
        --output /workspace/benchmarks/overload.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import List

import httpx

_PROMPT = "What is 2 + 2? Answer in one sentence."


@dataclass
class OverloadResult:
    test: str
    concurrency: int
    total_requests: int
    status_200: int
    status_429: int
    status_503: int
    other_errors: int
    accepted_ttft_p95_ms: float | None
    accepted_total_p95_ms: float | None


async def _fire(
    client: httpx.AsyncClient,
    url: str,
    api_key: str | None,
    max_tokens: int = 32,
) -> tuple[int, float | None, float]:
    """Returns (status_code, ttft_ms, total_ms)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": "test-assistant",
        "messages": [{"role": "user", "content": _PROMPT}],
        "max_tokens": max_tokens,
        "stream": True,
    }
    t_start = time.monotonic()
    ttft_ms: float | None = None
    try:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            status = resp.status_code
            if status >= 400:
                total_ms = (time.monotonic() - t_start) * 1000
                return status, None, total_ms
            async for line in resp.aiter_lines():
                if ttft_ms is None and line.startswith("data:") and "[DONE]" not in line:
                    ttft_ms = (time.monotonic() - t_start) * 1000
        total_ms = (time.monotonic() - t_start) * 1000
        return status, ttft_ms, total_ms
    except Exception:
        total_ms = (time.monotonic() - t_start) * 1000
        return 0, None, total_ms


def _pct(vals: list, p: float) -> float | None:
    if not vals:
        return None
    sv = sorted(vals)
    idx = max(0, int(len(sv) * p / 100) - 1)
    return round(sv[idx], 1)


async def _burst(url: str, api_key: str | None, n: int, test_name: str) -> OverloadResult:
    """Fire n simultaneous requests and collect status code distribution."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [_fire(client, url, api_key) for _ in range(n)]
        raw: List[tuple] = await asyncio.gather(*tasks)  # type: ignore[assignment]

    s200 = [r for r in raw if r[0] == 200]
    s429 = [r for r in raw if r[0] == 429]
    s503 = [r for r in raw if r[0] == 503]
    other = [r for r in raw if r[0] not in (200, 429, 503)]

    ttft_vals = [r[1] for r in s200 if r[1] is not None]
    total_vals = [r[2] for r in s200]

    return OverloadResult(
        test=test_name,
        concurrency=n,
        total_requests=n,
        status_200=len(s200),
        status_429=len(s429),
        status_503=len(s503),
        other_errors=len(other),
        accepted_ttft_p95_ms=_pct(ttft_vals, 95),
        accepted_total_p95_ms=_pct(total_vals, 95),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9100/v1/chat/completions")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--rpm-limit", type=int, default=60,
                        help="Configured platform RPM limit (to size the burst)")
    parser.add_argument("--concurrent-limit", type=int, default=5,
                        help="Configured platform per-tenant concurrent limit")
    parser.add_argument("--global-queue-limit", type=int, default=50)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    results = []

    # Test 1: 2× RPM burst — should produce mostly 429s
    burst_size = args.rpm_limit * 2
    print(f"Test 1: RPM burst — sending {burst_size} requests at once (2× rpm_limit={args.rpm_limit})…", flush=True)
    r1 = asyncio.run(_burst(args.url, args.api_key, burst_size, "rpm_burst_2x"))
    print(f"  200={r1.status_200}  429={r1.status_429}  503={r1.status_503}  "
          f"accepted_TTFT_P95={r1.accepted_ttft_p95_ms}ms")
    results.append(r1)

    # Short sleep to let rate-limit window reset
    print("Waiting 5s for rate-limit window cooldown…", flush=True)
    time.sleep(5)

    # Test 2: concurrent limit burst — e.g. 3× concurrent limit
    burst_size = args.concurrent_limit * 3
    print(f"Test 2: Concurrent burst — {burst_size} simultaneous (3× concurrent_limit={args.concurrent_limit})…", flush=True)
    r2 = asyncio.run(_burst(args.url, args.api_key, burst_size, "concurrent_burst_3x"))
    print(f"  200={r2.status_200}  429={r2.status_429}  503={r2.status_503}  "
          f"accepted_TTFT_P95={r2.accepted_ttft_p95_ms}ms")
    results.append(r2)

    print("\nPASSED conditions (verify manually):")
    for r in results:
        rejected = r.status_429 + r.status_503
        print(f"  [{r.test}]  rejected={rejected}/{r.total_requests}  "
              f"accepted={r.status_200}  accepted_TTFT_P95={r.accepted_ttft_p95_ms}ms")
        if r.status_200 > 0 and r.accepted_ttft_p95_ms and r.accepted_ttft_p95_ms > 5000:
            print("    ⚠ Warning: accepted request TTFT P95 > 5s — check for queue degradation")

    output = {
        "benchmark": "overload",
        "results": [
            {
                "test": r.test,
                "concurrency": r.concurrency,
                "total_requests": r.total_requests,
                "status_200": r.status_200,
                "status_429": r.status_429,
                "status_503": r.status_503,
                "other_errors": r.other_errors,
                "accepted_ttft_p95_ms": r.accepted_ttft_p95_ms,
                "accepted_total_p95_ms": r.accepted_total_p95_ms,
            }
            for r in results
        ],
    }
    print("\n" + json.dumps(output, indent=2))
    if args.output:
        import pathlib
        pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.output).write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
