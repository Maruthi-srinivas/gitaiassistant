from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from app.config import get_settings, validate_runtime_security
from app.database import SessionLocal, init_db
from app.models import IndexingJob, JobStatus
from app.redis_client import get_redis
from app.services.indexing_pipeline import run_indexing_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("indexing_worker")


def heartbeat_key(job_id: str) -> str:
    return f"index_heartbeat:{job_id}"


def _queue_keys(settings) -> tuple[str, str, str]:
    q = settings.index_queue_key
    return q, f"{q}:processing", f"{q}:dlq"


def _requeue_or_dlq(
    r,
    data: dict,
    processing_key: str,
    queue_key: str,
    dlq_key: str,
    max_attempts: int,
    original: str | None = None,
) -> None:
    raw = original if original is not None else json.dumps(data)
    r.lrem(processing_key, 1, raw)
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


def start_heartbeat(r, job_id: str, ttl: int, stop: threading.Event) -> threading.Thread:
    key = heartbeat_key(job_id)
    r.setex(key, ttl, "1")

    def beat() -> None:
        interval = max(1, ttl // 3)
        while not stop.wait(interval):
            r.setex(key, ttl, "1")
        r.delete(key)

    thread = threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True)
    thread.start()
    return thread


def reclaim_stale_processing(
    r,
    processing_key: str,
    queue_key: str,
    dlq_key: str,
    max_attempts: int,
) -> int:
    """Requeue processing jobs whose heartbeat key has expired."""
    reclaimed = 0
    items = r.lrange(processing_key, 0, -1) or []
    for item in items:
        raw = item if isinstance(item, str) else item.decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        job_id = str(data.get("job_id") or "")
        if not job_id or r.exists(heartbeat_key(job_id)):
            continue
        _requeue_or_dlq(r, data, processing_key, queue_key, dlq_key, max_attempts, original=raw)
        reclaimed += 1
    return reclaimed


def main() -> None:
    settings = get_settings()
    validate_runtime_security(settings)
    init_db()
    r = get_redis()
    queue_key, processing_key, dlq_key = _queue_keys(settings)
    max_attempts = int(getattr(settings, "index_max_attempts", 3) or 3)
    ttl = int(getattr(settings, "index_heartbeat_ttl_seconds", 30) or 30)

    leftover = r.lrange(processing_key, 0, -1)
    for item in leftover or []:
        r.rpush(queue_key, item)
        r.lrem(processing_key, 1, item)
    logger.info("Worker started. Listening on queue %s", queue_key)

    while True:
        try:
            item = r.blpop(queue_key, timeout=5)
            if not item:
                reclaim_stale_processing(r, processing_key, queue_key, dlq_key, max_attempts)
                continue
            _, payload = item
            raw_payload = payload if isinstance(payload, str) else payload.decode("utf-8")
            r.lpush(processing_key, raw_payload)
            data = json.loads(raw_payload)
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
            stop = threading.Event()
            start_heartbeat(r, str(job_id), ttl, stop)
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
                r.lrem(processing_key, 1, raw_payload)
            else:
                _requeue_or_dlq(
                    r, data, processing_key, queue_key, dlq_key, max_attempts, original=raw_payload
                )
            stop.set()
        except Exception:  # noqa: BLE001
            logger.exception("Worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
