from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.models import CodeChunk, FileRecord, Symbol
from app.rag.embeddings import embed_texts
from app.services.markdown_chunking import chunk_markdown

logger = logging.getLogger(__name__)


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
                db.add(
                    CodeChunk(
                        file_id=f.id,
                        repository_id=repository_id,
                        content=body,
                        start_line=start,
                        end_line=end,
                        language="markdown",
                        commit_hash=commit_hash,
                        tsv=body[:2000],
                    )
                )
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
                db.add(
                    CodeChunk(
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
                        tsv=body[:2000],
                    )
                )
                created += 1
        else:
            window = 80
            step = 60
            for start in range(0, max(1, len(lines)), step):
                end = min(len(lines), start + window)
                body = "\n".join(lines[start:end])
                if not body.strip():
                    continue
                db.add(
                    CodeChunk(
                        file_id=f.id,
                        repository_id=repository_id,
                        content=body,
                        start_line=start + 1,
                        end_line=end,
                        language=f.language,
                        commit_hash=commit_hash,
                        tsv=body[:2000],
                    )
                )
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
    texts = [c.content[:6000] for c in chunks]
    try:
        vectors = embed_texts(texts)
    except Exception as exc:  # noqa: BLE001
        logger.error("Embedding failed: %s", exc)
        raise
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector
    db.flush()
    return len(chunks)
