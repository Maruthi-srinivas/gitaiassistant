from __future__ import annotations

import logging
import random
import time
from typing import Callable, Sequence, TypeVar

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from app.config import get_settings
from app.services.usage_service import current_usage_user, record_usage

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (RateLimitError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        code = int(getattr(exc, "status_code", 0) or 0)
        return code == 429 or code >= 500
    return False


def call_with_backoff(fn: Callable[[], T], attempts: int = 4) -> T:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt >= attempts - 1 or not _retryable(exc):
                raise
            wait = min(2 ** (attempt + 1), 8) + random.random()
            logger.warning("LLM call failed (%s); retrying in %.2fs", exc, wait)
            time.sleep(wait)
    raise last or RuntimeError("LLM call failed")


def get_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.llm_api_key or "missing", base_url=settings.llm_base_url)


def _persist_usage(kind: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    user_id = current_usage_user()
    if not user_id:
        return
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        record_usage(db, user_id, kind, model, prompt_tokens, completion_tokens)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record %s usage: %s", kind, exc)
        db.rollback()
    finally:
        db.close()


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    client = get_openai_client()
    out: list[list[float]] = []
    batch_size = 32
    for i in range(0, len(texts), batch_size):
        batch = list(texts[i : i + batch_size])
        resp = call_with_backoff(
            lambda: client.embeddings.create(model=settings.embedding_model, input=batch)
        )
        ordered = sorted(resp.data, key=lambda d: d.index)
        out.extend([d.embedding for d in ordered])
        usage = getattr(resp, "usage", None)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        if prompt:
            _persist_usage("embedding", settings.embedding_model, prompt, 0)
    return out


def chat_completion(messages: list[dict], temperature: float = 0.2) -> str:
    settings = get_settings()
    client = get_openai_client()
    resp = call_with_backoff(
        lambda: client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=settings.llm_max_tokens,
        )
    )
    usage = getattr(resp, "usage", None)
    if usage:
        _persist_usage(
            "chat",
            settings.llm_model,
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )
    return resp.choices[0].message.content or ""


def chat_completion_stream(messages: list[dict], temperature: float = 0.2):
    settings = get_settings()
    client = get_openai_client()
    stream = call_with_backoff(
        lambda: client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=settings.llm_max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
    )
    prompt_tokens = 0
    completion_tokens = 0
    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0].delta, "content", None) or ""
        if delta:
            yield delta
    if prompt_tokens or completion_tokens:
        _persist_usage("chat", settings.llm_model, prompt_tokens, completion_tokens)
