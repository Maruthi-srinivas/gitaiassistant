from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openrouter/free"
    llm_max_tokens: int = 1024
    embedding_model: str = "openai/text-embedding-3-small"
    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/app_db"
    redis_url: str = "redis://redis:6379/0"
    github_token: str = ""
    data_dir: str = "/data"
    index_queue_key: str = "index_jobs"
    cors_origins: str = "http://localhost:3000"
    embedding_dimensions: int = 1536
    auth_rate_limit_per_minute: int = 10
    chat_rate_limit_per_minute: int = 20
    index_rate_limit_per_minute: int = 10
    max_active_index_jobs_per_user: int = 2
    free_daily_token_budget: int = 100_000
    free_monthly_token_budget: int = 2_000_000
    pro_daily_token_budget: int = 1_000_000
    pro_monthly_token_budget: int = 20_000_000
    git_clone_depth: int = 200
    git_history_max_commits: int = 200
    # RAG retrieval: heuristic (default) or llm for optional cross-chunk scoring
    rerank_mode: str = "heuristic"
    retrieve_candidates: int = 40
    context_chunks: int = 10
    jwt_secret: str = "dev-jwt-secret-change-me"
    jwt_expire_minutes: int = 1440
    encryption_key: str = ""
    environment: str = "development"
    index_max_attempts: int = 3


def validate_runtime_security(settings: Settings) -> None:
    if settings.environment.strip().lower() != "production":
        return
    if settings.jwt_secret == "dev-jwt-secret-change-me" or len(settings.jwt_secret.strip()) < 32:
        raise RuntimeError(
            "Production environment requires a strong JWT_SECRET (at least 32 characters)."
        )
    if not settings.encryption_key.strip():
        raise RuntimeError("Production environment requires an explicit ENCRYPTION_KEY.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
