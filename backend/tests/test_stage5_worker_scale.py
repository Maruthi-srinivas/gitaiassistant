from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import RateLimitError

from app.parsers.base import ParseResult
from app.rag.embeddings import call_with_backoff
from app.services.parser_service import parse_file_content
from app.services.repository_service import create_repository
from app.workers.indexing_worker import heartbeat_key, reclaim_stale_processing


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


@patch("app.workers.indexing_worker.time.sleep")
def test_stale_processing_job_is_requeued(mock_sleep):
    payload = json.dumps({"job_id": "job-1", "attempts": 0})
    redis = MagicMock()
    redis.lrange.return_value = [payload]
    redis.exists.return_value = 0

    reclaimed = reclaim_stale_processing(redis, "index_jobs:processing", "index_jobs", "index_jobs:dlq", 3)

    assert reclaimed == 1
    redis.lrem.assert_called_once_with("index_jobs:processing", 1, payload)
    redis.rpush.assert_called_once()
    queued = json.loads(redis.rpush.call_args[0][1])
    assert queued["attempts"] == 1
    assert queued["job_id"] == "job-1"
    mock_sleep.assert_called_once()


def test_live_heartbeat_is_not_reclaimed():
    payload = json.dumps({"job_id": "job-live", "attempts": 0})
    redis = MagicMock()
    redis.lrange.return_value = [payload]
    redis.exists.return_value = 1

    reclaimed = reclaim_stale_processing(redis, "index_jobs:processing", "index_jobs", "index_jobs:dlq", 3)

    assert reclaimed == 0
    redis.exists.assert_called_with(heartbeat_key("job-live"))
    redis.rpush.assert_not_called()


@patch("app.services.repository_service.get_settings")
@patch("app.services.repository_service.fetch_repo_metadata")
def test_oversized_repo_rejected(mock_meta, mock_settings):
    mock_meta.return_value = {"private": False, "default_branch": "main", "size": 600_000}
    mock_settings.return_value.github_token = ""
    mock_settings.return_value.max_repo_size_kb = 512_000
    db = MagicMock()
    with pytest.raises(ValueError, match="512000 KB"):
        create_repository(db, "https://github.com/acme/huge")
    db.add.assert_not_called()


@patch("app.rag.embeddings.time.sleep")
@patch("app.rag.embeddings.random.random", return_value=0.1)
def test_backoff_retries_then_succeeds(_jitter, mock_sleep):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _rate_limit_error()
        return "ok"

    assert call_with_backoff(flaky) == "ok"
    assert calls["n"] == 2
    mock_sleep.assert_called_once()
    assert mock_sleep.call_args[0][0] == pytest.approx(2.1)


def test_parse_file_content_returns_symbols():
    parsed = parse_file_content("python", "pkg/app.py", "def hello():\n    return 1\n")
    assert isinstance(parsed, ParseResult)
    assert any(sym.name == "hello" for sym in parsed.symbols)
