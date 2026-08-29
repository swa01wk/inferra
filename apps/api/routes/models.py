from fastapi import APIRouter, Depends

from apps.api.services.auth.keys import AuthenticatedContext, require_inference_key
from apps.api.services.vllm.client import VLLMClient

router = APIRouter()


@router.get("/models")
async def list_models(auth: AuthenticatedContext = Depends(require_inference_key)) -> dict:
    client = VLLMClient()
    return await client.list_models()
