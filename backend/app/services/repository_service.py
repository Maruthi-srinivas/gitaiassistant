from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IndexingJob, JobStatus, RepoStatus, Repository
from app.redis_client import get_redis
from app.services.github_service import fetch_repo_metadata, parse_github_url

logger = logging.getLogger(__name__)


def create_repository(db: Session, url: str) -> Repository:
    parsed = parse_github_url(url)
    meta = fetch_repo_metadata(parsed.owner, parsed.name)
    if meta.get("private"):
        raise ValueError("Only public repositories are supported in V1")
    repo = Repository(
        github_url=parsed.html_url,
        owner=parsed.owner,
        name=parsed.name,
        default_branch=meta.get("default_branch"),
        status=RepoStatus.CREATED,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def list_repositories(db: Session) -> list[Repository]:
    return db.query(Repository).order_by(Repository.created_at.desc()).all()


def get_repository(db: Session, repo_id: uuid.UUID) -> Repository:
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def enqueue_index(db: Session, repo_id: uuid.UUID, incremental: bool = False) -> IndexingJob:
    settings = get_settings()
    repo = get_repository(db, repo_id)
    if incremental and not repo.commit_hash:
        incremental = False

    job = IndexingJob(
        repository_id=repo.id,
        status=JobStatus.QUEUED,
        progress=0.0,
        incremental=incremental,
    )
    repo.status = RepoStatus.QUEUED
    repo.error = None
    db.add(job)
    db.commit()
    db.refresh(job)

    payload = json.dumps(
        {
            "job_id": str(job.id),
            "repository_id": str(repo.id),
            "incremental": incremental,
        }
    )
    get_redis().rpush(settings.index_queue_key, payload)
    logger.info("Enqueued index job %s for repo %s", job.id, repo.id)
    return job


def latest_job(db: Session, repo_id: uuid.UUID) -> IndexingJob | None:
    return (
        db.query(IndexingJob)
        .filter(IndexingJob.repository_id == repo_id)
        .order_by(IndexingJob.created_at.desc())
        .first()
    )


def check_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    r = get_redis()
    now = int(time.time())
    bucket = f"rl:{key}:{now // window_seconds}"
    count = r.incr(bucket)
    if count == 1:
        r.expire(bucket, window_seconds)
    if count > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
