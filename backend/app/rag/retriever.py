from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CodeChunk, FileRecord, Symbol
from app.rag.embeddings import embed_texts
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.reranker import rerank
from app.services.graph_service import expand_graph_neighbors

logger = logging.getLogger(__name__)


def _chunk_to_dict(chunk: CodeChunk, file_path: str, score: float = 0.0) -> dict:
    return {
        "id": str(chunk.id),
        "file": file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "content": chunk.content,
        "class_name": chunk.class_name,
        "method_name": chunk.method_name,
        "language": chunk.language,
        "score": score,
    }


def vector_search(db: Session, repository_id: uuid.UUID, query: str, limit: int = 8) -> list[dict]:
    try:
        embedding = embed_texts([query])[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("vector_search embed failed: %s", exc)
        return []
    sql = text(
        """
        SELECT c.id, c.content, c.start_line, c.end_line, c.class_name, c.method_name,
               c.language, f.path,
               (c.embedding <=> CAST(:embedding AS vector)) AS distance
        FROM code_chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.repository_id = :repo_id AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
        """
    )
    embedding_literal = "[" + ",".join(str(float(x)) for x in embedding) + "]"
    rows = db.execute(
        sql,
        {"embedding": embedding_literal, "repo_id": str(repository_id), "limit": limit},
    ).mappings().all()
    results = []
    for r in rows:
        results.append(
            {
                "id": str(r["id"]),
                "file": r["path"],
                "start_line": r["start_line"],
                "end_line": r["end_line"],
                "content": r["content"],
                "class_name": r["class_name"],
                "method_name": r["method_name"],
                "language": r["language"],
                "score": 1.0 / (1.0 + float(r["distance"] or 0)),
            }
        )
    return results


def keyword_search(db: Session, repository_id: uuid.UUID, query: str, limit: int = 8) -> list[dict]:
    """Postgres full-text search using simple config (preserves identifiers)."""
    cleaned = " ".join(re.findall(r"[A-Za-z0-9_\.]+", query))
    if len(cleaned) < 2:
        return []
    sql = text(
        """
        SELECT c.id, c.content, c.start_line, c.end_line, c.class_name, c.method_name,
               c.language, f.path,
               ts_rank_cd(c.search_tsv, plainto_tsquery('simple', :q)) AS rank
        FROM code_chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.repository_id = :repo_id
          AND c.search_tsv @@ plainto_tsquery('simple', :q)
        ORDER BY rank DESC
        LIMIT :limit
        """
    )
    try:
        rows = db.execute(
            sql,
            {"q": cleaned, "repo_id": str(repository_id), "limit": limit},
        ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("FTS keyword_search failed, falling back to ILIKE: %s", exc)
        return _keyword_search_ilike_fallback(db, repository_id, query, limit=limit)

    return [
        {
            "id": str(r["id"]),
            "file": r["path"],
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "content": r["content"],
            "class_name": r["class_name"],
            "method_name": r["method_name"],
            "language": r["language"],
            "score": float(r["rank"] or 0) + 0.5,
        }
        for r in rows
    ]


def _keyword_search_ilike_fallback(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 8
) -> list[dict]:
    tokens = [t for t in re.split(r"\W+", query) if len(t) > 2][:5]
    if not tokens:
        return []
    filters = [CodeChunk.content.ilike(f"%{t}%") for t in tokens]
    rows = (
        db.query(CodeChunk, FileRecord.path)
        .join(FileRecord, FileRecord.id == CodeChunk.file_id)
        .filter(CodeChunk.repository_id == repository_id, or_(*filters))
        .limit(limit)
        .all()
    )
    return [_chunk_to_dict(c, path, score=0.6) for c, path in rows]


def symbol_search(db: Session, repository_id: uuid.UUID, query: str, limit: int = 8) -> list[dict]:
    tokens = [t for t in re.split(r"\W+", query) if len(t) > 1]
    if not tokens:
        return []
    sym_filters = [Symbol.name.ilike(f"%{t}%") for t in tokens]
    symbols = (
        db.query(Symbol, FileRecord)
        .join(FileRecord, FileRecord.id == Symbol.file_id)
        .filter(FileRecord.repository_id == repository_id, or_(*sym_filters))
        .limit(limit)
        .all()
    )
    results: list[dict] = []
    for sym, file_rec in symbols:
        chunk = (
            db.query(CodeChunk)
            .filter(
                CodeChunk.file_id == file_rec.id,
                CodeChunk.start_line <= sym.start_line,
                CodeChunk.end_line >= sym.end_line,
            )
            .first()
        )
        if chunk:
            results.append(_chunk_to_dict(chunk, file_rec.path, score=1.2))
            continue
        lines = (file_rec.content or "").splitlines()
        snippet = "\n".join(lines[max(0, sym.start_line - 1) : sym.end_line])
        results.append(
            {
                "id": str(sym.id),
                "file": file_rec.path,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
                "content": snippet,
                "class_name": None,
                "method_name": sym.name,
                "language": file_rec.language,
                "score": 1.2,
            }
        )
    return results


def search_documentation(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 8
) -> list[dict]:
    """Vector + FTS retrieval restricted to markdown documentation chunks."""
    try:
        embedding = embed_texts([query])[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("search_documentation embed failed: %s", exc)
        embedding = None

    vector_hits: list[dict] = []
    if embedding is not None:
        sql = text(
            """
            SELECT c.id, c.content, c.start_line, c.end_line, c.class_name, c.method_name,
                   c.language, f.path,
                   (c.embedding <=> CAST(:embedding AS vector)) AS distance
            FROM code_chunks c
            JOIN files f ON f.id = c.file_id
            WHERE c.repository_id = :repo_id
              AND c.embedding IS NOT NULL
              AND c.language = 'markdown'
            ORDER BY c.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )
        embedding_literal = "[" + ",".join(str(float(x)) for x in embedding) + "]"
        rows = db.execute(
            sql,
            {"embedding": embedding_literal, "repo_id": str(repository_id), "limit": limit},
        ).mappings().all()
        for r in rows:
            vector_hits.append(
                {
                    "id": str(r["id"]),
                    "file": r["path"],
                    "start_line": r["start_line"],
                    "end_line": r["end_line"],
                    "content": r["content"],
                    "class_name": r["class_name"],
                    "method_name": r["method_name"],
                    "language": r["language"],
                    "score": 1.0 / (1.0 + float(r["distance"] or 0)),
                }
            )

    fts_hits = [
        h for h in keyword_search(db, repository_id, query, limit=limit) if h.get("language") == "markdown"
    ]
    # If FTS returned mixed languages, also filter via a dedicated query when possible
    if not fts_hits:
        cleaned = " ".join(re.findall(r"[A-Za-z0-9_\.]+", query))
        if cleaned:
            try:
                sql = text(
                    """
                    SELECT c.id, c.content, c.start_line, c.end_line, c.class_name, c.method_name,
                           c.language, f.path,
                           ts_rank_cd(c.search_tsv, plainto_tsquery('simple', :q)) AS rank
                    FROM code_chunks c
                    JOIN files f ON f.id = c.file_id
                    WHERE c.repository_id = :repo_id
                      AND c.language = 'markdown'
                      AND c.search_tsv @@ plainto_tsquery('simple', :q)
                    ORDER BY rank DESC
                    LIMIT :limit
                    """
                )
                rows = db.execute(
                    sql, {"q": cleaned, "repo_id": str(repository_id), "limit": limit}
                ).mappings().all()
                fts_hits = [
                    {
                        "id": str(r["id"]),
                        "file": r["path"],
                        "start_line": r["start_line"],
                        "end_line": r["end_line"],
                        "content": r["content"],
                        "class_name": r["class_name"],
                        "method_name": r["method_name"],
                        "language": r["language"],
                        "score": float(r["rank"] or 0) + 0.7,
                    }
                    for r in rows
                ]
            except Exception:  # noqa: BLE001
                pass

    merged = reciprocal_rank_fusion([vector_hits, fts_hits], limit=limit * 2)
    return rerank(merged, query, limit=limit)


def hybrid_retrieve(db: Session, repository_id: uuid.UUID, query: str, limit: int | None = None) -> list[dict]:
    settings = get_settings()
    cand = settings.retrieve_candidates
    context_limit = limit if limit is not None else settings.context_chunks

    vector_hits = vector_search(db, repository_id, query, limit=cand)
    keyword_hits = keyword_search(db, repository_id, query, limit=cand)
    symbol_hits = symbol_search(db, repository_id, query, limit=min(cand, 20))

    names = []
    for hit in symbol_hits:
        if hit.get("method_name"):
            names.append(hit["method_name"])
    expanded = expand_graph_neighbors(db, repository_id, names, hops=1)
    graph_hits: list[dict] = []
    if expanded:
        related = (
            db.query(Symbol, FileRecord)
            .join(FileRecord, FileRecord.id == Symbol.file_id)
            .filter(FileRecord.repository_id == repository_id, Symbol.name.in_(expanded[:20]))
            .limit(min(cand, 20))
            .all()
        )
        for sym, file_rec in related:
            lines = (file_rec.content or "").splitlines()
            snippet = "\n".join(lines[max(0, sym.start_line - 1) : min(len(lines), sym.end_line)])
            graph_hits.append(
                {
                    "id": str(sym.id),
                    "file": file_rec.path,
                    "start_line": sym.start_line,
                    "end_line": sym.end_line,
                    "content": snippet,
                    "class_name": None,
                    "method_name": sym.name,
                    "language": file_rec.language,
                    "score": 0.9,
                }
            )

    merged = reciprocal_rank_fusion(
        [vector_hits, keyword_hits, symbol_hits, graph_hits],
        limit=cand,
    )
    return rerank(merged, query, limit=context_limit)
