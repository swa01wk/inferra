from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    stream: bool = False
    max_tokens: int | None = 512
    temperature: float | None = 1.0
    top_p: float | None = 1.0
    enable_thinking: bool | None = None  # Qwen3 thinking mode; maps to chat_template_kwargs


class ChatCompletionChoice(BaseModel):
    index: int
    message: Message
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage | None = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "inferra"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


class CreateApiKeyRequest(BaseModel):
    name: str
    organization_id: UUID | None = None
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    id: UUID
    key_prefix: str
    name: str
    organization_id: UUID
    status: str
    expires_at: datetime | None = None


class ApiKeyCreateResponse(ApiKeyResponse):
    secret: str


class UsageQueryResponse(BaseModel):
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    requests: list[dict]


class CreateAdapterRequest(BaseModel):
    name: str
    storage_uri: str
    base_model: str = "Qwen/Qwen3-4B"
    rank: int = 16
    alias: str | None = None


class AdapterResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    base_model_id: UUID
    storage_uri: str
    rank: int
    status: str
    error_message: str | None = None
    created_at: datetime


class CreateAliasRequest(BaseModel):
    alias: str
    adapter_id: UUID | None = None
    base_model: str = "Qwen/Qwen3-4B"


class AliasResponse(BaseModel):
    id: UUID
    alias: str
    organization_id: UUID
    adapter_id: UUID | None
    base_model_id: UUID


class ErrorResponse(BaseModel):
    error: dict = Field(default_factory=dict)
