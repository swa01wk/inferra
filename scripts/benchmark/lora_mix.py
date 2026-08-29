#!/usr/bin/env python3
"""
LoRA mix benchmark.

Measures latency when requests are split between the base model and a LoRA adapter.
Requires at least one registered, active adapter alias in the platform.

Usage:
    python scripts/benchmark/lora_mix.py \\
        --url http://localhost:9100/v1/chat/completions \\
        --api-key inf_... \\
        --base-alias test-assistant \\
        --lora-alias lora-assistant \\
        --concurrency 4 8 \\
        --output /workspace/benchmarks/lora_mix.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import List

import httpx

_PROMPT = "Explain the key considerations when selecting a machine learning model for production inference."


@dataclass
class MixResult:
    alias: str
    is_lora: bool
    ttft_ms: float | None
    total_ms: float
    completion_tokens: int
    error: str | None = None


async def _single(
    client: httpx.AsyncClient,
    url: str,
    api_key: str | None,
    alias: str,
    is_lora: bool,
    max_tokens: int,
) -> MixResult:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": alias,
        "messages": [{"role": "user", "content": _PROMPT}],
        "max_tokens": max_tokens,
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
    return MixResult(
        alias=alias, is_lora=is_lora, ttft_ms=ttft_ms,
        total_ms=total_ms, completion_tokens=completion_tokens, error=error,
    )


def _pct(vals: list, p: float) -> float | None:
    if not vals:
        return None
    sv = sorted(vals)
    idx = max(0, int(len(sv) * p / 100) - 1)
    return round(sv[idx], 1)


async def run_mix(
    url: str, api_key: str | None,
    base_alias: str, lora_alias: str,
    concurrency: int, lora_fraction: float,
    max_tokens: int, repeats: int,
) -> dict:
    """Run `concurrency` simultaneous requests with `lora_fraction` going to the LoRA alias."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        tasks = []
        for _ in range(repeats):
            batch = []
            for i in range(concurrency):
                use_lora = random.random() < lora_fraction
                alias = lora_alias if use_lora else base_alias
                batch.append(_single(client, url, api_key, alias, use_lora, max_tokens))
            tasks.extend(batch)

        results: List[MixResult] = await asyncio.gather(*tasks)  # type: ignore[assignment]

    base_results = [r for r in results if not r.is_lora and r.error is None]
    lora_results = [r for r in results if r.is_lora and r.error is None]

    return {
        "concurrency": concurrency,
        "lora_fraction": lora_fraction,
        "total_requests": len(results),
        "errors": sum(1 for r in results if r.error is not None),
        "base_ttft_p95_ms": _pct([r.ttft_ms for r in base_results if r.ttft_ms], 95),
        "base_total_p95_ms": _pct([r.total_ms for r in base_results], 95),
        "lora_ttft_p95_ms": _pct([r.ttft_ms for r in lora_results if r.ttft_ms], 95),
        "lora_total_p95_ms": _pct([r.total_ms for r in lora_results], 95),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9100/v1/chat/completions")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-alias", default="test-assistant")
    parser.add_argument("--lora-alias", default="lora-assistant",
                        help="An active LoRA adapter alias in the platform")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[4, 8])
    parser.add_argument("--lora-fractions", type=float, nargs="+", default=[0.0, 0.5, 1.0])
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    results = []
    for c in args.concurrency:
        for frac in args.lora_fractions:
            label = f"concurrency={c} lora_fraction={frac}"
            print(f"Running {label}…", flush=True)
            cell = asyncio.run(run_mix(
                args.url, args.api_key, args.base_alias, args.lora_alias,
                c, frac, args.max_tokens, args.repeats,
            ))
            results.append(cell)
            print(
                f"  base_TTFT_P95={cell['base_ttft_p95_ms']}ms  "
                f"lora_TTFT_P95={cell['lora_ttft_p95_ms']}ms  "
                f"errors={cell['errors']}"
            )

    output = {"benchmark": "lora_mix", "results": results}
    print("\n" + json.dumps(output, indent=2))
    if args.output:
        import pathlib
        pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.output).write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
