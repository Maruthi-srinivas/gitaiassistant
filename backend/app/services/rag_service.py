from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import CodeChunk, FileRecord, Symbol
from app.rag.chunk_text import build_chunk_tsv, build_embed_text
from app.rag.embeddings import embed_texts
from app.services.markdown_chunking import chunk_markdown

logger = logging.getLogger(__name__)

# Re-export for callers that imported from rag_service
__all__ = [
    "build_chunk_tsv",
    "build_embed_text",
    "clear_chunks",
    "chunk_repository",
    "embed_chunks",
    "sync_chunk_search_tsv",
]


def sync_chunk_search_tsv(db: Session, chunk_id: uuid.UUID, tsv_text: str) -> None:
    """Update Postgres tsvector column (simple config preserves identifiers)."""
    db.execute(
        text(
            "UPDATE code_chunks SET search_tsv = to_tsvector('simple', :tsv) WHERE id = :id"
        ),
        {"tsv": tsv_text or "", "id": str(chunk_id)},
    )


def clear_chunks(db: Session, repository_id: uuid.UUID, file_ids: list[uuid.UUID] | None = None) -> None:
    q = db.query(CodeChunk).filter(CodeChunk.repository_id == repository_id)
    if file_ids is not None:
        if not file_ids:
            return
        q = q.filter(CodeChunk.file_id.in_(file_ids))
    q.delete(synchronize_session=False)
    db.flush()


def chunk_repository(
    db: Session,
    repository_id: uuid.UUID,
    commit_hash: str | None,
    file_ids: list[uuid.UUID] | None = None,
) -> int:
    clear_chunks(db, repository_id, file_ids=file_ids)
    q = db.query(FileRecord).filter(FileRecord.repository_id == repository_id)
    if file_ids is not None:
        q = q.filter(FileRecord.id.in_(file_ids))
    files = q.all()
    created = 0
    for f in files:
        lines = (f.content or "").splitlines()
        if f.language == "markdown":
            for start, end, body in chunk_markdown(f.content or ""):
                tsv = build_chunk_tsv(f.path, body, language="markdown")
                chunk = CodeChunk(
                    file_id=f.id,
                    repository_id=repository_id,
                    content=body,
                    start_line=start,
                    end_line=end,
                    language="markdown",
                    commit_hash=commit_hash,
                    tsv=tsv,
                )
                db.add(chunk)
                db.flush()
                sync_chunk_search_tsv(db, chunk.id, tsv)
                created += 1
            continue

        symbols = db.query(Symbol).filter(Symbol.file_id == f.id).all()
        if symbols:
            for sym in symbols:
                if sym.type not in {"class", "function", "method"}:
                    continue
                body = "\n".join(lines[max(0, sym.start_line - 1) : sym.end_line])
                if not body.strip():
                    continue
                class_name = (
                    sym.name.split(".")[0]
                    if "." in sym.name
                    else (sym.name if sym.type == "class" else None)
                )
                method_name = (
                    sym.name.split(".")[-1] if sym.type in {"function", "method"} else None
                )
                tsv = build_chunk_tsv(
                    f.path,
                    body,
                    class_name=class_name,
                    method_name=method_name,
                    language=f.language,
                )
                chunk = CodeChunk(
                    file_id=f.id,
                    repository_id=repository_id,
                    symbol_id=sym.id,
                    content=body,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    class_name=class_name,
                    method_name=method_name,
                    language=f.language,
                    commit_hash=commit_hash,
                    tsv=tsv,
                )
                db.add(chunk)
                db.flush()
                sync_chunk_search_tsv(db, chunk.id, tsv)
                created += 1
        else:
            window = 80
            step = 40
            for start in range(0, max(1, len(lines)), step):
                end = min(len(lines), start + window)
                body = "\n".join(lines[start:end])
                if not body.strip():
                    continue
                tsv = build_chunk_tsv(f.path, body, language=f.language)
                chunk = CodeChunk(
                    file_id=f.id,
                    repository_id=repository_id,
                    content=body,
                    start_line=start + 1,
                    end_line=end,
                    language=f.language,
                    commit_hash=commit_hash,
                    tsv=tsv,
                )
                db.add(chunk)
                db.flush()
                sync_chunk_search_tsv(db, chunk.id, tsv)
                created += 1
                if end >= len(lines):
                    break
    db.flush()
    return created


def embed_chunks(
    db: Session, repository_id: uuid.UUID, file_ids: list[uuid.UUID] | None = None
) -> int:
    q = db.query(CodeChunk).filter(
        CodeChunk.repository_id == repository_id, CodeChunk.embedding.is_(None)
    )
    if file_ids is not None:
        if not file_ids:
            return 0
        q = q.filter(CodeChunk.file_id.in_(file_ids))
    chunks = q.all()
    if not chunks:
        return 0

    file_ids_needed = {c.file_id for c in chunks}
    files = {
        f.id: f
        for f in db.query(FileRecord).filter(FileRecord.id.in_(file_ids_needed)).all()
    }
    texts: list[str] = []
    for c in chunks:
        path = files[c.file_id].path if c.file_id in files else ""
        texts.append(
            build_embed_text(
                path,
                c.content or "",
                class_name=c.class_name,
                method_name=c.method_name,
                language=c.language,
            )
        )
    try:
        vectors = embed_texts(texts)
    except Exception as exc:  # noqa: BLE001
        logger.error("Embedding failed: %s", exc)
        raise
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector
    db.flush()
    return len(chunks)
