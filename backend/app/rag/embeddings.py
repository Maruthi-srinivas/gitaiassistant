from __future__ import annotations

import logging
from typing import Sequence

from openai import OpenAI

from app.config import get_settings
from app.services.usage_service import current_usage_user, record_usage

logger = logging.getLogger(__name__)


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
        resp = client.embeddings.create(model=settings.embedding_model, input=batch)
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
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
        max_tokens=settings.llm_max_tokens,
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
