#!/usr/bin/env python3
"""
Single-request baseline benchmark — Phase 8 Stage 1.

Runs one request per prompt profile (non-streaming) and measures:
  - Total end-to-end latency
  - Prompt tokens and completion tokens
  - Output tokens per second

Also runs a streaming request to measure TTFT directly.

Usage:
    # Against gateway (local dev)
    python scripts/benchmark/baseline.py \\
        --url http://localhost:9100/v1/chat/completions \\
        --api-key inf_... \\
        --output /workspace/benchmarks/baseline.json

    # Against vLLM directly (on pod, no gateway)
    python scripts/benchmark/baseline.py \\
        --url http://localhost:8000/v1/chat/completions \\
        --model Qwen/Qwen3-4B
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.request

# Prompt profiles matching Phase 8 spec §24.1
_PROFILES = [
    {
        "label": "short_chat",
        "prompt": "What is KV-cache in a transformer model? Answer in two sentences.",
        "max_tokens": 128,
        "target_prompt_tokens": 256,
    },
    {
        "label": "medium_chat",
        "prompt": (
            "You are an expert in machine learning infrastructure. "
            "Explain the trade-offs between BF16 and FP8 KV-cache quantization for "
            "LLM inference on a 24 GB GPU. Cover memory savings, quality impact, "
            "and when you would choose one over the other. "
        ) * 3,
        "max_tokens": 256,
        "target_prompt_tokens": 512,
    },
    {
        "label": "long_prompt",
        "prompt": (
            "The following is a technical analysis of transformer inference optimization. "
            "Attention mechanisms process query, key, and value matrices. "
            "PagedAttention divides the KV-cache into fixed-size pages to reduce fragmentation. "
            "Prefix caching reuses shared prompt prefixes across requests. "
            "Continuous batching interleaves decoding steps from multiple requests. "
        ) * 10
        + "\n\nSummarise the three most impactful optimisations described above.",
        "max_tokens": 256,
        "target_prompt_tokens": 1024,
    },
    {
        "label": "upper_v1_context",
        "prompt": (
            "This document describes a large industrial control system with many subsystems. "
            "Each subsystem has sensors, actuators, and a local PLC. "
            "The central SCADA collects data every 100ms and runs anomaly detection. "
        ) * 20
        + "\n\nList the three most important safety considerations for this system.",
        "max_tokens": 256,
        "target_prompt_tokens": 2048,
    },
]


def _run_non_streaming(
    url: str,
    model: str,
    api_key: str | None,
    prompt: str,
    max_tokens: int,
) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )

    t_start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode())
    total_s = time.perf_counter() - t_start

    usage = body.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    tps = round(completion_tokens / total_s, 1) if total_s > 0 and completion_tokens > 0 else 0.0
    finish_reason = (body.get("choices") or [{}])[0].get("finish_reason")

    return {
        "total_ms": round(total_s * 1000, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tokens_per_second": tps,
        "finish_reason": finish_reason,
    }


def _run_streaming_ttft(
    url: str,
    model: str,
    api_key: str | None,
) -> dict:
    """Send a streaming request and measure TTFT directly."""
    import http.client
    import socket
    from urllib.parse import urlparse

    prompt = "Explain what attention is in one sentence."
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": 64,
        "temperature": 0.0,
    }).encode()

    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path

    headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t_start = time.perf_counter()
    t_first_token: float | None = None
    completion_tokens = 0
    done_received = False
    chunks = 0

    try:
        conn = http.client.HTTPConnection(host, timeout=120)
        conn.request("POST", path, body=payload, headers=headers)
        resp = conn.getresponse()

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                done_received = True
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if choices and (choices[0].get("delta") or {}).get("content"):
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                chunks += 1
            usage = chunk.get("usage") or {}
            if usage.get("completion_tokens"):
                completion_tokens = usage["completion_tokens"]
        conn.close()
    except Exception as exc:
        return {"error": str(exc), "ttft_ms": None, "streaming_ok": False}

    t_end = time.perf_counter()
    ttft_ms = round((t_first_token - t_start) * 1000, 1) if t_first_token else None
    total_ms = round((t_end - t_start) * 1000, 1)
    decode_ms = round((t_end - t_first_token) * 1000, 1) if t_first_token else None
    tps = round(completion_tokens / (decode_ms / 1000), 1) if decode_ms and decode_ms > 0 and completion_tokens > 0 else None

    return {
        "ttft_ms": ttft_ms,
        "decode_ms": decode_ms,
        "total_ms": total_ms,
        "completion_tokens": completion_tokens,
        "tokens_per_second": tps,
        "chunks": chunks,
        "done_received": done_received,
        "streaming_ok": done_received and ttft_ms is not None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:9100/v1/chat/completions",
                        help="Chat completions endpoint URL")
    parser.add_argument("--api-key", default=None, help="Bearer API key (inference key)")
    parser.add_argument("--model", default="test-assistant",
                        help="Model alias or HF repo ID")
    parser.add_argument("--output", default=None,
                        help="Write JSON results to this file (used by report.py)")
    args = parser.parse_args()

    print(f"Baseline benchmark → {args.url}")
    print(f"Model: {args.model}")
    print("")

    results = []

    # Non-streaming profiles
    for profile in _PROFILES:
        label = profile["label"]
        print(f"Profile: {label}  (max_tokens={profile['max_tokens']})…", flush=True)
        try:
            r = _run_non_streaming(
                args.url, args.model, args.api_key,
                profile["prompt"], profile["max_tokens"],
            )
            r["label"] = label
            results.append(r)
            print(
                f"  total={r['total_ms']}ms  "
                f"prompt={r['prompt_tokens']}  "
                f"completion={r['completion_tokens']}  "
                f"tok/s={r['tokens_per_second']}  "
                f"finish={r['finish_reason']}"
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append({"label": label, "error": str(exc)})

    # Streaming TTFT
    print("")
    print("Streaming TTFT measurement…", flush=True)
    try:
        stream_result = _run_streaming_ttft(args.url, args.model, args.api_key)
        stream_result["label"] = "streaming_ttft"
        results.append(stream_result)
        print(
            f"  TTFT={stream_result.get('ttft_ms')}ms  "
            f"decode={stream_result.get('decode_ms')}ms  "
            f"total={stream_result.get('total_ms')}ms  "
            f"tok/s={stream_result.get('tokens_per_second')}  "
            f"streaming_ok={stream_result.get('streaming_ok')}"
        )
    except Exception as exc:
        print(f"  ERROR: {exc}")
        results.append({"label": "streaming_ttft", "error": str(exc)})

    output = {"benchmark": "baseline", "model": args.model, "url": args.url, "results": results}
    print("")
    print(json.dumps(output, indent=2))

    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(output, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
