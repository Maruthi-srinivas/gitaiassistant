from __future__ import annotations

import logging
import time
import uuid

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
    User,
)
from app.services.file_discovery import (
    changed_files,
    clone_repository,
    discover_files,
    pull_or_fetch,
    repo_local_path,
)
from app.services.git_history_service import extract_git_history, sync_branches
from app.services.github_service import parse_github_url
from app.services.graph_service import rebuild_graph
from app.services.knowledge_service import enrich_knowledge_descriptions, rebuild_knowledge_tree
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


def run_indexing_job(
    db: Session,
    job_id: uuid.UUID,
    incremental: bool = False,
    branch: str | None = None,
) -> bool:
    job = db.get(IndexingJob, job_id)
    if not job:
        logger.error("Job %s not found", job_id)
        return False
    repo = db.get(Repository, job.repository_id)
    if not repo:
        logger.error("Repository for job %s not found", job_id)
        return False

    timings: dict[str, float] = {}
    metrics: dict = {}
    t0 = time.perf_counter()

    try:
        parsed = parse_github_url(repo.github_url)
        dest = repo_local_path(str(repo.id))
        owner = db.get(User, repo.owner_user_id) if repo.owner_user_id else None
        clone_token = owner.github_token if owner and owner.github_token else None
        old_commit = repo.commit_hash
        target_branch = branch or job.branch or repo.default_branch
        if branch:
            job.branch = branch
        elif not job.branch:
            job.branch = target_branch

        _set_status(db, repo, job, JobStatus.CLONING, 0.1)
        clone_start = time.perf_counter()
        if dest.exists() and (dest / ".git").exists() and incremental and old_commit:
            try:
                old_c, new_c = pull_or_fetch(dest, branch=target_branch)
                changed = changed_files(dest, old_c, new_c)
                commit_hash = new_c
            except Exception as fetch_exc:  # noqa: BLE001
                logger.warning(
                    "Incremental fetch failed for repo %s (%s); falling back to full clone",
                    repo.id,
                    fetch_exc,
                )
                commit_hash = clone_repository(parsed, dest, branch=target_branch, token=clone_token)
                changed = None
                incremental = False
                job.incremental = False
        else:
            # Branch switch or first index: full clone/checkout
            if (
                dest.exists()
                and (dest / ".git").exists()
                and target_branch
                and not incremental
                and branch
                and branch != repo.default_branch
            ):
                # Reuse clone when switching branch
                try:
                    from app.services.file_discovery import checkout_branch

                    commit_hash = checkout_branch(dest, target_branch)
                    changed = None
                    incremental = False
                except Exception:  # noqa: BLE001
                    commit_hash = clone_repository(parsed, dest, branch=target_branch, token=clone_token)
                    changed = None
                    incremental = False
            else:
                commit_hash = clone_repository(parsed, dest, branch=target_branch, token=clone_token)
                changed = None
                incremental = False

        timings["clone_ms"] = round((time.perf_counter() - clone_start) * 1000, 1)
        repo.local_path = str(dest)
        repo.commit_hash = commit_hash
        if target_branch:
            repo.default_branch = target_branch
        db.commit()

        _set_status(db, repo, job, JobStatus.ANALYZING, 0.25)
        hist_start = time.perf_counter()
        try:
            sync_branches(db, repo.id, dest, target_branch, commit_hash)
            hist_metrics = extract_git_history(db, repo.id, dest, incremental=incremental)
            metrics.update(hist_metrics)
        except Exception as hist_exc:  # noqa: BLE001
            logger.warning("Git history extraction failed for %s: %s", repo.id, hist_exc)
            metrics["history_error"] = str(hist_exc)[:500]
        timings["history_ms"] = round((time.perf_counter() - hist_start) * 1000, 1)
        db.commit()

        discovered = discover_files(dest)
        if changed is not None:
            changed_set = set(changed)
            discovered = [f for f in discovered if f["path"] in changed_set]

        _set_status(db, repo, job, JobStatus.PARSING, 0.4)
        parse_start = time.perf_counter()
        affected_file_ids: list[uuid.UUID] = []

        if incremental and changed is not None:
            existing = (
                db.query(FileRecord)
                .filter(FileRecord.repository_id == repo.id, FileRecord.path.in_(changed))
                .all()
            )
            _delete_file_artifacts(db, [f.id for f in existing])

        if not incremental:
            db.query(CodeChunk).filter(CodeChunk.repository_id == repo.id).delete(
                synchronize_session=False
            )
            db.query(Dependency).filter(Dependency.repository_id == repo.id).delete(
                synchronize_session=False
            )
            file_ids = [f.id for f in db.query(FileRecord).filter(FileRecord.repository_id == repo.id).all()]
            if file_ids:
                db.query(Symbol).filter(Symbol.file_id.in_(file_ids)).delete(synchronize_session=False)
            db.query(GraphEdge).filter(GraphEdge.repository_id == repo.id).delete(
                synchronize_session=False
            )
            db.query(GraphNode).filter(GraphNode.repository_id == repo.id).delete(
                synchronize_session=False
            )
            db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == repo.id).delete(
                synchronize_session=False
            )
            db.query(FileRecord).filter(FileRecord.repository_id == repo.id).delete(
                synchronize_session=False
            )
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
            if item["language"] != "markdown":
                parsed_result = parse_file_content(item["language"], item["path"], item["content"])
                persist_parse_results(db, repo.id, file_rec, parsed_result)
            affected_file_ids.append(file_rec.id)
        db.commit()
        timings["parse_ms"] = round((time.perf_counter() - parse_start) * 1000, 1)
        metrics["files"] = len(affected_file_ids)

        _set_status(db, repo, job, JobStatus.GRAPH_BUILDING, 0.6)
        graph_start = time.perf_counter()
        rebuild_graph(db, repo.id)
        rebuild_knowledge_tree(db, repo.id)
        try:
            enrich_knowledge_descriptions(
                db,
                repo.id,
                file_paths=[
                    f.path
                    for f in db.query(FileRecord)
                    .filter(FileRecord.id.in_(affected_file_ids))
                    .all()
                ]
                if incremental
                else None,
            )
        except Exception as enrich_exc:  # noqa: BLE001
            logger.warning("Knowledge enrichment failed: %s", enrich_exc)
        db.commit()
        timings["graph_ms"] = round((time.perf_counter() - graph_start) * 1000, 1)

        _set_status(db, repo, job, JobStatus.CHUNKING, 0.75)
        chunk_start = time.perf_counter()
        chunk_count = chunk_repository(
            db,
            repo.id,
            commit_hash,
            file_ids=affected_file_ids if incremental else None,
        )
        db.commit()
        timings["chunk_ms"] = round((time.perf_counter() - chunk_start) * 1000, 1)
        metrics["chunks"] = chunk_count

        _set_status(db, repo, job, JobStatus.EMBEDDING, 0.9)
        embed_start = time.perf_counter()
        embedded = embed_chunks(db, repo.id, file_ids=affected_file_ids if incremental else None)
        db.commit()
        timings["embed_ms"] = round((time.perf_counter() - embed_start) * 1000, 1)
        metrics["embedded"] = embedded

        timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        job.timings = timings
        job.metrics = metrics
        db.commit()

        _set_status(db, repo, job, JobStatus.COMPLETED, 1.0)
        logger.info(
            "Indexing completed for repo %s job %s timings=%s metrics=%s",
            repo.id,
            job.id,
            timings,
            metrics,
        )
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
            job.timings = timings or {"total_ms": round((time.perf_counter() - t0) * 1000, 1)}
            job.metrics = metrics or {}
            _set_status(db, repo, job, JobStatus.FAILED, job.progress or 0.0, error=msg[:2000])
        return False
    return True
