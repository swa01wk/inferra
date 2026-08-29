from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Inferra Inference Platform"
    app_version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 9000
    log_level: str = "INFO"

    vllm_base_url: str = "http://vllm:8000"
    vllm_timeout_seconds: int = 120

    default_max_tokens: int = 512
    max_context_tokens: int = 8192
    default_context_tokens: int = 4096

    postgres_dsn: str = "postgresql+asyncpg://inferra:inferra@postgres:5432/inferra"
    redis_url: str = "redis://redis:6379/0"

    admin_secret: str = "dev-admin-secret-change-me"

    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "inferra-adapters"
    adapter_cache_dir: str = "/tmp/inferra-adapters"

    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"

    max_lora_rank: int = 16
    global_queue_limit: int = 50


settings = Settings()
