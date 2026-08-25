from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IndexingJob, JobStatus, RepoStatus, Repository, User
from app.redis_client import get_redis
from app.request_context import get_request_id
from app.services.github_service import fetch_repo_metadata, parse_github_url

logger = logging.getLogger(__name__)


def create_repository(db: Session, url: str, owner: User | None = None) -> Repository:
    parsed = parse_github_url(url)
    settings = get_settings()
    token = (owner.github_token if owner and owner.github_token else None) or settings.github_token
    meta = fetch_repo_metadata(parsed.owner, parsed.name, token=token)
    if meta.get("private") and not token:
        raise ValueError("Private repositories require GITHUB_TOKEN")
    repo = Repository(
        github_url=parsed.html_url,
        owner=parsed.owner,
        name=parsed.name,
        default_branch=meta.get("default_branch"),
        status=RepoStatus.CREATED,
        owner_user_id=owner.id if owner else None,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


def list_repositories(db: Session, user_id: uuid.UUID | None = None) -> list[Repository]:
    q = db.query(Repository)
    if user_id is not None:
        q = q.filter(Repository.owner_user_id == user_id)
    return q.order_by(Repository.created_at.desc()).all()


def get_repository(db: Session, repo_id: uuid.UUID, user_id: uuid.UUID | None = None) -> Repository:
    repo = db.get(Repository, repo_id)
    if not repo or (user_id is not None and repo.owner_user_id != user_id):
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def enqueue_index(
    db: Session,
    repo_id: uuid.UUID,
    incremental: bool = False,
    branch: str | None = None,
) -> IndexingJob:
    settings = get_settings()
    repo = get_repository(db, repo_id)
    if incremental and not repo.commit_hash:
        incremental = False
    # Switching branch forces full re-index
    if branch and repo.default_branch and branch != repo.default_branch:
        incremental = False

    job = IndexingJob(
        repository_id=repo.id,
        status=JobStatus.QUEUED,
        progress=0.0,
        incremental=incremental,
        branch=branch or repo.default_branch,
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
            "branch": branch or repo.default_branch,
            "attempts": 0,
            "request_id": get_request_id(),
        }
    )
    get_redis().rpush(settings.index_queue_key, payload)
    logger.info("Enqueued index job %s for repo %s branch=%s", job.id, repo.id, branch)
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
