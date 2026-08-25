from __future__ import annotations

import json
import logging
import time
import uuid

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import IndexingJob, JobStatus
from app.redis_client import get_redis
from app.services.indexing_pipeline import run_indexing_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("indexing_worker")


def _queue_keys(settings) -> tuple[str, str, str]:
    q = settings.index_queue_key
    return q, f"{q}:processing", f"{q}:dlq"


def _requeue_or_dlq(r, data: dict, processing_key: str, queue_key: str, dlq_key: str, max_attempts: int) -> None:
    payload = json.dumps(data)
    r.lrem(processing_key, 1, payload)
    attempts = int(data.get("attempts") or 0) + 1
    data["attempts"] = attempts
    updated = json.dumps(data)
    if attempts < max_attempts:
        delay = min(2 ** attempts, 8)
        logger.warning("Retrying job %s attempt %s in %ss", data.get("job_id"), attempts, delay)
        time.sleep(delay)
        r.rpush(queue_key, updated)
    else:
        logger.error("Moving job %s to DLQ after %s attempts", data.get("job_id"), attempts)
        r.rpush(dlq_key, updated)


def main() -> None:
    settings = get_settings()
    init_db()
    r = get_redis()
    queue_key, processing_key, dlq_key = _queue_keys(settings)
    max_attempts = int(getattr(settings, "index_max_attempts", 3) or 3)

    leftover = r.lrange(processing_key, 0, -1)
    for item in leftover or []:
        r.rpush(queue_key, item)
        r.lrem(processing_key, 1, item)
    logger.info("Worker started. Listening on queue %s", queue_key)

    while True:
        try:
            item = r.blpop(queue_key, timeout=5)
            if not item:
                continue
            _, payload = item
            r.lpush(processing_key, payload)
            data = json.loads(payload)
            job_id = uuid.UUID(data["job_id"])
            incremental = bool(data.get("incremental", False))
            branch = data.get("branch")
            logger.info(
                json.dumps(
                    {
                        "event": "index_job_start",
                        "job_id": str(job_id),
                        "request_id": data.get("request_id"),
                        "incremental": incremental,
                        "branch": branch,
                        "attempts": data.get("attempts", 0),
                    }
                )
            )
            db = SessionLocal()
            ok = False
            try:
                ok = bool(run_indexing_job(db, job_id, incremental=incremental, branch=branch))
                if not ok:
                    job = db.get(IndexingJob, job_id)
                    ok = bool(job and job.status == JobStatus.COMPLETED)
            finally:
                db.close()
            if ok:
                r.lrem(processing_key, 1, payload)
            else:
                _requeue_or_dlq(r, data, processing_key, queue_key, dlq_key, max_attempts)
        except Exception:  # noqa: BLE001
            logger.exception("Worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
