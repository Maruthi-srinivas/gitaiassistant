from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import router
from app.main import app
from app.models import User
from app.rag.embeddings import chat_completion
from app.services.repository_service import assert_index_capacity, check_rate_limit
from app.services.usage_service import assert_token_budget, reset_usage_user, set_usage_user


@patch("app.services.repository_service.get_redis")
def test_rate_limit_raises_after_window_cap(mock_get_redis):
    fake = MagicMock()
    fake.incr.side_effect = [1, 2]
    mock_get_redis.return_value = fake
    check_rate_limit("auth:127.0.0.1", limit=1)
    with pytest.raises(HTTPException) as exc:
        check_rate_limit("auth:127.0.0.1", limit=1)
    assert exc.value.status_code == 429


@patch("app.services.repository_service.get_settings")
def test_index_capacity_blocks_at_cap(mock_settings):
    mock_settings.return_value.max_active_index_jobs_per_user = 2
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.count.return_value = 2
    with pytest.raises(HTTPException) as exc:
        assert_index_capacity(db, uuid.uuid4())
    assert exc.value.status_code == 429


@patch("app.services.usage_service.tokens_used_since", return_value=100_000)
def test_token_budget_free_vs_pro(_used):
    user = User(id=uuid.uuid4(), email="a@b.com", password_hash="x", plan="free")
    with pytest.raises(HTTPException) as exc:
        assert_token_budget(MagicMock(), user)
    assert exc.value.status_code == 429

    user.plan = "pro"
    assert_token_budget(MagicMock(), user)


@patch("app.database.SessionLocal")
@patch("app.rag.embeddings.get_openai_client")
def test_chat_completion_records_usage(mock_client, mock_session):
    resp = MagicMock()
    resp.choices[0].message.content = "hello"
    resp.usage.prompt_tokens = 11
    resp.usage.completion_tokens = 7
    mock_client.return_value.chat.completions.create.return_value = resp
    db = MagicMock()
    mock_session.return_value = db
    user_id = uuid.uuid4()
    token = set_usage_user(user_id)
    try:
        assert chat_completion([{"role": "user", "content": "hi"}]) == "hello"
    finally:
        reset_usage_user(token)
    added = db.add.call_args[0][0]
    assert added.user_id == user_id
    assert added.kind == "chat"
    assert added.prompt_tokens == 11
    assert added.completion_tokens == 7
    db.commit.assert_called_once()


def test_api_v1_prefix_and_health():
    assert router.prefix == "/api/v1"
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/health" in paths
    mounted = {getattr(route, "path", "") for route in router.routes}
    assert "/api/v1/auth/login" in mounted
    assert "/api/v1/repositories/{repo_id}/chat" in mounted
