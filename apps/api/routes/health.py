from fastapi import APIRouter
from fastapi.responses import JSONResponse

from apps.api.services.vllm.client import VLLMClient

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    client = VLLMClient()
    vllm_ok = await client.health()
    body = {
        "status": "ok" if vllm_ok else "degraded",
        "vllm": "ready" if vllm_ok else "unavailable",
    }
    return JSONResponse(content=body, status_code=200 if vllm_ok else 503)
