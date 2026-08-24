from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    *,
    k: int = 60,
    limit: int | None = None,
) -> list[dict]:
    """Merge ranked result lists with Reciprocal Rank Fusion.

    score = sum(1 / (k + rank_i)) across lists. Dedupes by file:start:end.
    """
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}

    for results in ranked_lists:
        for rank, item in enumerate(results, start=1):
            key = f"{item.get('file')}:{item.get('start_line')}:{item.get('end_line')}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best:
                best[key] = dict(item)

    for key, item in best.items():
        item["score"] = scores[key]

    ranked = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    if limit is not None:
        return ranked[:limit]
    return ranked
