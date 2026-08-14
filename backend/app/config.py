from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4o-mini"
    embedding_model: str = "openai/text-embedding-3-small"
    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/app_db"
    redis_url: str = "redis://redis:6379/0"
    github_token: str = ""
    data_dir: str = "/data"
    index_queue_key: str = "index_jobs"
    cors_origins: str = "http://localhost:3000"
    embedding_dimensions: int = 1536
    chat_rate_limit_per_minute: int = 30
    index_rate_limit_per_minute: int = 10
    git_clone_depth: int = 200
    git_history_max_commits: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
