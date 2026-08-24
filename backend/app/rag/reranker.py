from __future__ import annotations

import json
import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)

_DOC_HINTS = (
    "readme",
    "documentation",
    "docs",
    "how do i",
    "getting started",
    "install",
    "guide",
)


def _heuristic_score(candidate: dict, query: str, tokens: list[str]) -> float:
    score = float(candidate.get("score", 0.0))
    text = (candidate.get("content") or "").lower()
    path = (candidate.get("file") or "").lower()
    method = (candidate.get("method_name") or "").lower()
    class_name = (candidate.get("class_name") or "").lower()
    language = (candidate.get("language") or "").lower()
    q_lower = query.lower()
    wants_docs = any(h in q_lower for h in _DOC_HINTS)

    for t in tokens:
        if t == method or t == class_name:
            score += 1.5
        elif t in method or t in class_name:
            score += 0.8
        if t in path:
            score += 0.6
        if t in text:
            score += 0.25

    if wants_docs and language == "markdown":
        score += 0.7
    elif not wants_docs and language == "markdown":
        score -= 0.15

    # Prefer tighter, symbol-scoped chunks over huge windows
    start = int(candidate.get("start_line") or 1)
    end = int(candidate.get("end_line") or start)
    span = max(1, end - start + 1)
    if span <= 80:
        score += 0.2
    elif span > 200:
        score -= 0.15

    return score


def heuristic_rerank(candidates: list[dict], query: str, limit: int = 8) -> list[dict]:
    """Lexical / structural score fusion reranker."""
    q = query.lower()
    tokens = [
        t
        for t in q.replace("?", " ").replace("(", " ").replace(")", " ").split()
        if len(t) > 2
    ]
    scored: dict[str, dict] = {}
    for c in candidates:
        key = f"{c['file']}:{c['start_line']}:{c['end_line']}"
        score = _heuristic_score(c, query, tokens)
        if key not in scored or score > scored[key]["score"]:
            item = dict(c)
            item["score"] = score
            scored[key] = item
    ranked = sorted(scored.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


def llm_rerank(candidates: list[dict], query: str, limit: int = 8) -> list[dict]:
    """Optional LLM scoring of top candidates; falls back to heuristic on failure."""
    if not candidates:
        return []
    settings = get_settings()
    if not settings.llm_api_key:
        return heuristic_rerank(candidates, query, limit=limit)

    # Cap payload size
    pool = candidates[:20]
    payload = [
        {
            "i": i,
            "file": c.get("file"),
            "start": c.get("start_line"),
            "end": c.get("end_line"),
            "symbol": c.get("method_name") or c.get("class_name"),
            "snippet": (c.get("content") or "")[:400],
        }
        for i, c in enumerate(pool)
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "Score each code snippet for relevance to the question. "
                "Return a JSON array of objects with keys i (int) and score (0-10). "
                "No markdown."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {query}\n\nSnippets:\n{json.dumps(payload)}",
        },
    ]
    try:
        from app.rag.embeddings import chat_completion

        raw = chat_completion(messages, temperature=0.0)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("expected list")
        by_i = {int(item["i"]): float(item["score"]) for item in data if "i" in item}
        for i, c in enumerate(pool):
            base = float(c.get("score", 0.0))
            c["score"] = base + by_i.get(i, 0.0)
        ranked = sorted(pool, key=lambda x: x["score"], reverse=True)
        return ranked[:limit]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM rerank failed, using heuristic: %s", exc)
        return heuristic_rerank(candidates, query, limit=limit)


def rerank(candidates: list[dict], query: str, limit: int = 8) -> list[dict]:
    """Simple score fusion reranker (backward-compatible entrypoint)."""
    settings = get_settings()
    mode = (settings.rerank_mode or "heuristic").lower()
    if mode == "llm":
        # Heuristic first to order the pool, then LLM on top
        pre = heuristic_rerank(candidates, query, limit=max(limit, 20))
        return llm_rerank(pre, query, limit=limit)
    return heuristic_rerank(candidates, query, limit=limit)
