#!/usr/bin/env python3
"""
Context length sweep benchmark.

Measures TTFT and total latency at 2K / 4K / 8K context lengths
at concurrency=1, 4, and 8.

Usage:
    python scripts/benchmark/context_sweep.py \\
        --url http://localhost:9100/v1/chat/completions \\
        --api-key inf_... \\
        --output /workspace/benchmarks/context_sweep.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import List

import httpx

# Approximate words-per-token ratio is ~0.75; these prompts are sized accordingly.
_BASE_PARAGRAPH = (
    "The transformer architecture introduced the attention mechanism as a replacement for "
    "recurrent networks. Self-attention allows each token to attend to every other token "
    "in the sequence, enabling parallelism and long-range dependency modelling. "
)

def _build_prompt(target_tokens: int) -> str:
    """Build a prompt that approximates target_tokens by repeating a base paragraph."""
    words_per_repeat = len(_BASE_PARAGRAPH.split())
    # ~0.75 words/token → tokens_per_repeat ≈ words_per_repeat * 0.75
    tokens_per_repeat = words_per_repeat * 0.75
    repeats = max(1, int(target_tokens / tokens_per_repeat))
    prompt = (_BASE_PARAGRAPH * repeats).strip()
    prompt += "\n\nSummarise the key points above in three sentences."
    return prompt


@dataclass
class ContextResult:
    context_tokens: int
    concurrency: int
    ttft_ms: float | None
    total_ms: float
    completion_tokens: int
    error: str | None = None


async def _single(
    client: httpx.AsyncClient,
    url: str,
    api_key: str | None,
    context_tokens: int,
    concurrency: int,
    max_output_tokens: int,
) -> ContextResult:
    prompt = _build_prompt(context_tokens)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": "test-assistant",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_output_tokens,
        "stream": True,
    }
    t_start = time.monotonic()
    ttft_ms: float | None = None
    completion_tokens = 0
    error: str | None = None
    try:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if ttft_ms is None and line.startswith("data:") and "[DONE]" not in line:
                    ttft_ms = (time.monotonic() - t_start) * 1000
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        chunk = json.loads(line[5:].strip())
                        usage = chunk.get("usage") or {}
                        if usage.get("completion_tokens"):
                            completion_tokens = usage["completion_tokens"]
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        error = str(exc)
    total_ms = (time.monotonic() - t_start) * 1000
    return ContextResult(
        context_tokens=context_tokens,
        concurrency=concurrency,
        ttft_ms=ttft_ms,
        total_ms=total_ms,
        completion_tokens=completion_tokens,
        error=error,
    )


async def run_cell(
    url: str, api_key: str | None, context_tokens: int, concurrency: int,
    max_output_tokens: int, repeats: int,
) -> dict:
    async with httpx.AsyncClient(timeout=180.0) as client:
        tasks = [
            _single(client, url, api_key, context_tokens, concurrency, max_output_tokens)
            for _ in range(repeats)
        ]
        results: List[ContextResult] = await asyncio.gather(*tasks)  # type: ignore[assignment]

    successes = [r for r in results if r.error is None]
    ttft_vals = sorted(r.ttft_ms for r in successes if r.ttft_ms is not None)
    lat_vals = sorted(r.total_ms for r in successes)

    def pct(vals: list, p: float) -> float | None:
        if not vals:
            return None
        idx = max(0, int(len(vals) * p / 100) - 1)
        return round(vals[idx], 1)

    return {
        "context_tokens": context_tokens,
        "concurrency": concurrency,
        "requests": len(results),
        "errors": len(results) - len(successes),
        "ttft_p50_ms": pct(ttft_vals, 50),
        "ttft_p95_ms": pct(ttft_vals, 95),
        "total_latency_p95_ms": pct(lat_vals, 95),
        "kv_cache_note": "Read from Grafana vllm_gpu_cache_usage_perc at time of test",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9100/v1/chat/completions")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--context-sizes", type=int, nargs="+", default=[2048, 4096, 8192])
    parser.add_argument("--concurrency-levels", type=int, nargs="+", default=[1, 4, 8])
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=5, help="Requests per cell")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    results = []
    for ctx in args.context_sizes:
        for conc in args.concurrency_levels:
            print(f"context={ctx} concurrency={conc}…", flush=True)
            cell = asyncio.run(run_cell(
                args.url, args.api_key, ctx, conc,
                args.max_output_tokens, args.repeats,
            ))
            results.append(cell)
            print(f"  TTFT_P95={cell['ttft_p95_ms']}ms  lat_P95={cell['total_latency_p95_ms']}ms  errors={cell['errors']}")

    output = {"benchmark": "context_sweep", "results": results}
    print("\n" + json.dumps(output, indent=2))
    if args.output:
        import pathlib
        pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.output).write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
