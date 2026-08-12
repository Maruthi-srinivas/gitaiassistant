from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import (
    CodeChunk,
    Dependency,
    FileRecord,
    GraphEdge,
    GraphNode,
    IndexingJob,
    JobStatus,
    KnowledgeNode,
    RepoStatus,
    Repository,
    Symbol,
)
from app.services.file_discovery import (
    changed_files,
    clone_repository,
    discover_files,
    pull_or_fetch,
    repo_local_path,
)
from app.services.github_service import parse_github_url
from app.services.graph_service import rebuild_graph
from app.services.knowledge_service import rebuild_knowledge_tree
from app.services.parser_service import parse_file_content, persist_parse_results
from app.services.rag_service import chunk_repository, embed_chunks

logger = logging.getLogger(__name__)


def _set_status(
    db: Session,
    repo: Repository,
    job: IndexingJob,
    status: JobStatus,
    progress: float,
    error: str | None = None,
) -> None:
    job.status = status
    job.progress = progress
    job.error = error
    repo.status = RepoStatus(status.value)
    repo.error = error
    db.commit()


def _delete_file_artifacts(db: Session, file_ids: list[uuid.UUID]) -> None:
    if not file_ids:
        return
    db.query(CodeChunk).filter(CodeChunk.file_id.in_(file_ids)).delete(synchronize_session=False)
    db.query(Dependency).filter(Dependency.file_id.in_(file_ids)).delete(synchronize_session=False)
    db.query(Symbol).filter(Symbol.file_id.in_(file_ids)).delete(synchronize_session=False)
    db.query(FileRecord).filter(FileRecord.id.in_(file_ids)).delete(synchronize_session=False)
    db.flush()


def run_indexing_job(db: Session, job_id: uuid.UUID, incremental: bool = False) -> None:
    job = db.get(IndexingJob, job_id)
    if not job:
        logger.error("Job %s not found", job_id)
        return
    repo = db.get(Repository, job.repository_id)
    if not repo:
        logger.error("Repository for job %s not found", job_id)
        return

    try:
        parsed = parse_github_url(repo.github_url)
        dest = repo_local_path(str(repo.id))
        old_commit = repo.commit_hash

        _set_status(db, repo, job, JobStatus.CLONING, 0.1)
        if dest.exists() and (dest / ".git").exists() and incremental and old_commit:
            try:
                old_c, new_c = pull_or_fetch(dest)
                changed = changed_files(dest, old_c, new_c)
                commit_hash = new_c
            except Exception as fetch_exc:  # noqa: BLE001
                logger.warning(
                    "Incremental fetch failed for repo %s (%s); falling back to full clone",
                    repo.id,
                    fetch_exc,
                )
                commit_hash = clone_repository(parsed, dest)
                changed = None
                incremental = False
                job.incremental = False
        else:
            commit_hash = clone_repository(parsed, dest)
            changed = None
            incremental = False

        repo.local_path = str(dest)
        repo.commit_hash = commit_hash
        db.commit()

        _set_status(db, repo, job, JobStatus.ANALYZING, 0.25)
        discovered = discover_files(dest)
        if changed is not None:
            changed_set = set(changed)
            discovered = [f for f in discovered if f["path"] in changed_set]

        _set_status(db, repo, job, JobStatus.PARSING, 0.4)
        affected_file_ids: list[uuid.UUID] = []

        if incremental and changed is not None:
            existing = (
                db.query(FileRecord)
                .filter(FileRecord.repository_id == repo.id, FileRecord.path.in_(changed))
                .all()
            )
            _delete_file_artifacts(db, [f.id for f in existing])

        if not incremental:
            # Full reindex wipe of derived data
            db.query(CodeChunk).filter(CodeChunk.repository_id == repo.id).delete(synchronize_session=False)
            db.query(Dependency).filter(Dependency.repository_id == repo.id).delete(synchronize_session=False)
            file_ids = [f.id for f in db.query(FileRecord).filter(FileRecord.repository_id == repo.id).all()]
            if file_ids:
                db.query(Symbol).filter(Symbol.file_id.in_(file_ids)).delete(synchronize_session=False)
            db.query(GraphEdge).filter(GraphEdge.repository_id == repo.id).delete(synchronize_session=False)
            db.query(GraphNode).filter(GraphNode.repository_id == repo.id).delete(synchronize_session=False)
            db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == repo.id).delete(synchronize_session=False)
            db.query(FileRecord).filter(FileRecord.repository_id == repo.id).delete(synchronize_session=False)
            db.flush()
            discovered = discover_files(dest)

        for item in discovered:
            existing = (
                db.query(FileRecord)
                .filter(FileRecord.repository_id == repo.id, FileRecord.path == item["path"])
                .first()
            )
            if existing:
                existing.language = item["language"]
                existing.size = item["size"]
                existing.hash = item["hash"]
                existing.content = item["content"]
                file_rec = existing
            else:
                file_rec = FileRecord(
                    repository_id=repo.id,
                    path=item["path"],
                    language=item["language"],
                    size=item["size"],
                    hash=item["hash"],
                    content=item["content"],
                )
                db.add(file_rec)
                db.flush()
            parsed_result = parse_file_content(item["language"], item["path"], item["content"])
            persist_parse_results(db, repo.id, file_rec, parsed_result)
            affected_file_ids.append(file_rec.id)
        db.commit()

        _set_status(db, repo, job, JobStatus.GRAPH_BUILDING, 0.6)
        rebuild_graph(db, repo.id)
        rebuild_knowledge_tree(db, repo.id)
        db.commit()

        _set_status(db, repo, job, JobStatus.CHUNKING, 0.75)
        chunk_repository(
            db,
            repo.id,
            commit_hash,
            file_ids=affected_file_ids if incremental else None,
        )
        db.commit()

        _set_status(db, repo, job, JobStatus.EMBEDDING, 0.9)
        embed_chunks(db, repo.id, file_ids=affected_file_ids if incremental else None)
        db.commit()

        _set_status(db, repo, job, JobStatus.COMPLETED, 1.0)
        logger.info("Indexing completed for repo %s job %s", repo.id, job.id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Indexing failed for job %s", job_id)
        db.rollback()
        job = db.get(IndexingJob, job_id)
        repo = db.get(Repository, job.repository_id) if job else None
        if job and repo:
            msg = str(exc)
            if "fetch" in msg.lower() or "incremental" in msg.lower():
                msg = (
                    f"{msg} Click Analyze to run a full re-index of this repository."
                    if "Analyze" not in msg
                    else msg
                )
            _set_status(db, repo, job, JobStatus.FAILED, job.progress or 0.0, error=msg[:2000])
