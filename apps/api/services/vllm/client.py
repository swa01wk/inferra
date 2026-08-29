from __future__ import annotations

from typing import AsyncIterator

import httpx

from apps.api.config import settings


class VLLMClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self._base_url = (base_url or settings.vllm_base_url).rstrip("/")
        self._timeout = timeout or settings.vllm_timeout_seconds

    async def health(self) -> bool:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self._base_url}/health")
            return response.status_code == 200

    async def list_models(self) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/v1/models")
            response.raise_for_status()
            return response.json()

    async def chat_completions(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def chat_completions_stream(self, payload: dict) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

    async def load_lora_adapter(self, lora_name: str, lora_path: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/load_lora_adapter",
                json={"lora_name": lora_name, "lora_path": lora_path},
            )
            response.raise_for_status()
            return response.json()
