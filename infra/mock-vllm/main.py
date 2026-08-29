"""Mock vLLM server for local control-plane development without GPU."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Mock vLLM", version="1.0.0")

MOCK_MODEL = os.getenv("MOCK_MODEL", "Qwen/Qwen3-4B")
MOCK_COMPLETION_TOKENS = int(os.getenv("MOCK_COMPLETION_TOKENS", "32"))
MOCK_TOKEN_DELAY_MS = float(os.getenv("MOCK_TOKEN_DELAY_MS", "20"))
MOCK_TTFT_DELAY_MS = float(os.getenv("MOCK_TTFT_DELAY_MS", "50"))

_loaded_loras: set[str] = set()
_active_requests = 0


class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message] = Field(default_factory=list)
    stream: bool = False
    max_tokens: int | None = 512
    temperature: float | None = 1.0


class LoadLoraRequest(BaseModel):
    lora_name: str
    lora_path: str | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": MOCK_MODEL,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock-vllm",
            }
        ],
    }


def _estimate_prompt_tokens(messages: list[Message]) -> int:
    text = " ".join(m.content for m in messages)
    return max(1, len(text.split()))


def _build_completion_text(num_tokens: int) -> str:
    words = ["hello", "world", "inference", "mock", "token", "stream", "response"]
    return " ".join(words[i % len(words)] for i in range(num_tokens))


@app.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    global _active_requests
    _active_requests += 1
    try:
        prompt_tokens = _estimate_prompt_tokens(body.messages)
        completion_tokens = min(body.max_tokens or MOCK_COMPLETION_TOKENS, MOCK_COMPLETION_TOKENS)
        completion_text = _build_completion_text(completion_tokens)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        if body.stream:
            async def event_stream():
                await asyncio.sleep(MOCK_TTFT_DELAY_MS / 1000.0)
                words = completion_text.split()
                for idx, word in enumerate(words):
                    if await request.is_disconnected():
                        break
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": body.model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": word if idx == 0 else f" {word}"},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(MOCK_TOKEN_DELAY_MS / 1000.0)

                final = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": body.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": len(words),
                        "total_tokens": prompt_tokens + len(words),
                    },
                }
                yield f"data: {json.dumps(final)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(event_stream(), media_type="text/event-stream")

        await asyncio.sleep((MOCK_TTFT_DELAY_MS + completion_tokens * MOCK_TOKEN_DELAY_MS) / 1000.0)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": completion_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    finally:
        _active_requests -= 1


@app.post("/v1/load_lora_adapter")
async def load_lora_adapter(body: LoadLoraRequest) -> dict[str, str]:
    _loaded_loras.add(body.lora_name)
    return {"status": "loaded", "lora_name": body.lora_name}


@app.post("/v1/unload_lora_adapter")
async def unload_lora_adapter(body: LoadLoraRequest) -> dict[str, str]:
    _loaded_loras.discard(body.lora_name)
    return {"status": "unloaded", "lora_name": body.lora_name}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    lines = [
        "# HELP vllm_num_requests_running Number of requests running",
        "# TYPE vllm_num_requests_running gauge",
        f"vllm_num_requests_running {_active_requests}",
        "# HELP vllm_num_requests_waiting Number of requests waiting",
        "# TYPE vllm_num_requests_waiting gauge",
        "vllm_num_requests_waiting 0",
        "# HELP vllm_gpu_cache_usage_perc GPU KV cache usage percentage",
        "# TYPE vllm_gpu_cache_usage_perc gauge",
        "vllm_gpu_cache_usage_perc 0.35",
        "# HELP vllm_prefix_cache_hit_rate Prefix cache hit rate",
        "# TYPE vllm_prefix_cache_hit_rate gauge",
        "vllm_prefix_cache_hit_rate 0.42",
        "# HELP vllm_time_to_first_token_seconds TTFT histogram",
        "# TYPE vllm_time_to_first_token_seconds histogram",
        'vllm_time_to_first_token_seconds_bucket{le="0.1"} 10',
        'vllm_time_to_first_token_seconds_bucket{le="0.5"} 50',
        'vllm_time_to_first_token_seconds_bucket{le="+Inf"} 100',
        "vllm_time_to_first_token_seconds_sum 25.0",
        "vllm_time_to_first_token_seconds_count 100",
        "# HELP mock_loaded_loras Number of loaded LoRA adapters",
        "# TYPE mock_loaded_loras gauge",
        f"mock_loaded_loras {len(_loaded_loras)}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
