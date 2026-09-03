from __future__ import annotations

import logging
from typing import Sequence

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


def get_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.llm_api_key or "missing", base_url=settings.llm_base_url)


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    client = get_openai_client()
    # Batch to avoid payload limits
    out: list[list[float]] = []
    batch_size = 32
    for i in range(0, len(texts), batch_size):
        batch = list(texts[i : i + batch_size])
        resp = client.embeddings.create(model=settings.embedding_model, input=batch)
        # Ensure order
        ordered = sorted(resp.data, key=lambda d: d.index)
        out.extend([d.embedding for d in ordered])
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
    return resp.choices[0].message.content or ""
