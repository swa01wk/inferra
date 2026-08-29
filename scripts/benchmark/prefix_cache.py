#!/usr/bin/env python3
"""
Prefix cache effectiveness benchmark.

Sends requests with a long shared system prompt (prefix) and varying user suffixes.
Measures TTFT improvement when vLLM's prefix cache kicks in.

Usage:
    python scripts/benchmark/prefix_cache.py \\
        --url http://localhost:9100/v1/chat/completions \\
        --api-key inf_... \\
        --shared-prefix-tokens 2048 \\
        --output /workspace/benchmarks/prefix_cache.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import List

import httpx

_BASE_PARAGRAPH = (
    "The following is a comprehensive technical reference document for a large industrial "
    "manufacturing system. It covers safety procedures, maintenance protocols, operational "
    "thresholds, error codes, and escalation paths. Read it carefully before answering "
    "any questions. "
)

_USER_SUFFIXES = [
    "What is the recommended maintenance interval?",
    "List the top three safety procedures.",
    "What error code indicates a temperature warning?",
    "Describe the escalation path for a critical failure.",
    "How often should diagnostic checks be run?",
    "What is the maximum operational threshold for pressure?",
    "Summarise the key maintenance protocols in two sentences.",
    "What should an operator do if the system enters a fault state?",
]


def _build_system_prompt(target_tokens: int) -> str:
    words_per_repeat = len(_BASE_PARAGRAPH.split())
    tokens_per_repeat = words_per_repeat * 0.75
    repeats = max(1, int(target_tokens / tokens_per_repeat))
    return (_BASE_PARAGRAPH * repeats).strip()


@dataclass
class PrefixResult:
    request_index: int
    shared_prefix_tokens: int
    ttft_ms: float | None
    total_ms: float
    is_cached: bool
    error: str | None = None


async def _single(
    client: httpx.AsyncClient,
    url: str,
    api_key: str | None,
    system_prompt: str,
    user_suffix: str,
    request_index: int,
    shared_prefix_tokens: int,
    max_tokens: int,
) -> PrefixResult:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": "test-assistant",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_suffix},
        ],
        "max_tokens": max_tokens,
        "stream": True,
    }
    t_start = time.monotonic()
    ttft_ms: float | None = None
    error: str | None = None
    try:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if ttft_ms is None and line.startswith("data:") and "[DONE]" not in line:
                    ttft_ms = (time.monotonic() - t_start) * 1000
    except Exception as exc:
        error = str(exc)
    total_ms = (time.monotonic() - t_start) * 1000
    # First request = cold (no cache), subsequent = warm (cache should help)
    return PrefixResult(
        request_index=request_index,
        shared_prefix_tokens=shared_prefix_tokens,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        is_cached=request_index > 0,
        error=error,
    )


async def run_prefix_test(
    url: str, api_key: str | None, shared_prefix_tokens: int,
    max_tokens: int,
) -> dict:
    system_prompt = _build_system_prompt(shared_prefix_tokens)
    async with httpx.AsyncClient(timeout=180.0) as client:
        results: List[PrefixResult] = []
        for i, suffix in enumerate(_USER_SUFFIXES):
            r = await _single(client, url, api_key, system_prompt, suffix, i, shared_prefix_tokens, max_tokens)
            results.append(r)
            status = "cached" if r.is_cached else "cold"
            print(f"  [{status}] TTFT={r.ttft_ms}ms  total={r.total_ms:.0f}ms", flush=True)

    cold = [r for r in results if not r.is_cached and r.error is None]
    warm = [r for r in results if r.is_cached and r.error is None]
    cold_ttft = cold[0].ttft_ms if cold and cold[0].ttft_ms else None
    warm_ttft_vals = [r.ttft_ms for r in warm if r.ttft_ms is not None]
    warm_ttft_p50 = sorted(warm_ttft_vals)[len(warm_ttft_vals) // 2] if warm_ttft_vals else None

    improvement_pct: float | None = None
    if cold_ttft and warm_ttft_p50:
        improvement_pct = round((cold_ttft - warm_ttft_p50) / cold_ttft * 100, 1)

    return {
        "shared_prefix_tokens": shared_prefix_tokens,
        "cold_ttft_ms": round(cold_ttft, 1) if cold_ttft else None,
        "warm_ttft_p50_ms": round(warm_ttft_p50, 1) if warm_ttft_p50 else None,
        "ttft_improvement_pct": improvement_pct,
        "errors": sum(1 for r in results if r.error is not None),
        "note": "Check Grafana vllm_prefix_cache_hit_rate panel during this test",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9100/v1/chat/completions")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--shared-prefix-tokens", type=int, nargs="+", default=[2048])
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    results = []
    for prefix_tokens in args.shared_prefix_tokens:
        print(f"Prefix cache test — shared_prefix_tokens={prefix_tokens}…", flush=True)
        cell = asyncio.run(run_prefix_test(args.url, args.api_key, prefix_tokens, args.max_tokens))
        results.append(cell)
        print(
            f"  Cold TTFT={cell['cold_ttft_ms']}ms  "
            f"Warm P50 TTFT={cell['warm_ttft_p50_ms']}ms  "
            f"Improvement={cell['ttft_improvement_pct']}%"
        )

    output = {"benchmark": "prefix_cache", "results": results}
    print("\n" + json.dumps(output, indent=2))
    if args.output:
        import pathlib
        pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.output).write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
