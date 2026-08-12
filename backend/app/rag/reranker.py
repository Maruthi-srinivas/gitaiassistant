from __future__ import annotations

from collections import defaultdict


def rerank(candidates: list[dict], query: str, limit: int = 8) -> list[dict]:
    """Simple score fusion reranker."""
    q = query.lower()
    tokens = [t for t in q.replace("?", " ").replace("(", " ").replace(")", " ").split() if len(t) > 2]
    scored: dict[str, dict] = {}
    for c in candidates:
        key = f"{c['file']}:{c['start_line']}:{c['end_line']}"
        score = float(c.get("score", 0.0))
        text = (c.get("content") or "").lower()
        path = (c.get("file") or "").lower()
        for t in tokens:
            if t in text:
                score += 0.35
            if t in path:
                score += 0.5
            if t == (c.get("method_name") or "").lower() or t == (c.get("class_name") or "").lower():
                score += 1.0
        if key not in scored or score > scored[key]["score"]:
            item = dict(c)
            item["score"] = score
            scored[key] = item
    ranked = sorted(scored.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:limit]
