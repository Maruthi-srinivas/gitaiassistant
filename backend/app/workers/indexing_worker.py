from __future__ import annotations

import json
import logging
import time
import uuid

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.redis_client import get_redis
from app.services.indexing_pipeline import run_indexing_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("indexing_worker")


def main() -> None:
    settings = get_settings()
    init_db()
    r = get_redis()
    logger.info("Worker started. Listening on queue %s", settings.index_queue_key)
    while True:
        try:
            item = r.blpop(settings.index_queue_key, timeout=5)
            if not item:
                continue
            _, payload = item
            data = json.loads(payload)
            job_id = uuid.UUID(data["job_id"])
            incremental = bool(data.get("incremental", False))
            branch = data.get("branch")
            logger.info(
                "Processing job %s incremental=%s branch=%s", job_id, incremental, branch
            )
            db = SessionLocal()
            try:
                run_indexing_job(db, job_id, incremental=incremental, branch=branch)
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            logger.exception("Worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
