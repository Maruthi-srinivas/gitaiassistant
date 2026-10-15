from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

from app.agents.workflow import frames_for_tokens, status_for_node
from app.models import JobStatus
from app.services.indexing_pipeline import _set_status, format_index_sse, index_progress_channel


@patch("app.services.indexing_pipeline.get_redis")
def test_set_status_publishes_progress(mock_get_redis):
    client = MagicMock()
    mock_get_redis.return_value = client
    repo = MagicMock()
    repo.id = uuid.uuid4()
    repo.error = None
    repo.default_branch = "main"
    job = MagicMock()
    job.branch = "main"
    db = MagicMock()

    _set_status(db, repo, job, JobStatus.PARSING, 0.4, error=None)

    channel, raw = client.publish.call_args[0]
    body = json.loads(raw)
    assert channel == index_progress_channel(repo.id)
    assert body["status"] == "PARSING"
    assert body["progress"] == 0.4
    assert body["repository_id"] == str(repo.id)


def test_index_sse_frame_includes_data_prefix():
    frame = format_index_sse({"status": "CLONING", "progress": 0.1})
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    assert "CLONING" in frame


def test_status_for_node_labels():
    assert status_for_node("classify") == "Classifying the question..."
    assert status_for_node("retrieve") == "Searching symbols..."
    assert status_for_node("compose_answer") == "Composing the answer..."
    assert status_for_node("source_check") == "Verifying citations..."


def test_token_frames_precede_done():
    frames = frames_for_tokens(["Hel", "lo"], {"conversation_id": "abc", "answer": "Hello", "sources": []})
    assert frames[0].startswith("event: token")
    assert "Hel" in frames[0]
    assert frames[1].startswith("event: token")
    assert frames[-1].startswith("event: done")
    assert "Hello" in frames[-1]
