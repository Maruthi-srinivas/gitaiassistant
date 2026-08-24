"""Unit tests for RAG quality upgrades (no live LLM/DB required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.query_utils import (
    extract_symbol_hint,
    is_grounded,
    rewrite_query,
    ungrounded_refusal,
)
from app.rag.chunk_text import build_chunk_tsv, build_embed_text
from app.rag.fusion import reciprocal_rank_fusion


def test_rrf_prefers_relevant_over_noise():
    """Relevant doc that ranks well in multiple lists beats ILIKE-style noise."""
    relevant = {
        "file": "src/flask/app.py",
        "start_line": 10,
        "end_line": 40,
        "content": "def create_app(): ...",
        "method_name": "create_app",
        "score": 0.5,
    }
    noise = {
        "file": "tests/test_helpers.py",
        "start_line": 1,
        "end_line": 5,
        "content": "def create_tmp(): ...",
        "method_name": "create_tmp",
        "score": 0.9,
    }
    # Noise wins vector alone; relevant wins FTS + symbol
    vector = [noise, relevant]
    fts = [relevant, noise]
    symbol = [relevant]
    fused = reciprocal_rank_fusion([vector, fts, symbol], limit=5)
    assert fused[0]["file"] == "src/flask/app.py"
    assert fused[0]["method_name"] == "create_app"


def test_symbol_hint_skips_where():
    q = "Where is the application factory defined?"
    hint = extract_symbol_hint(q)
    assert hint is None or hint.lower() != "where"
    assert hint is None or hint.lower() not in {"where", "what", "the", "is"}


def test_symbol_hint_prefers_backticks():
    assert extract_symbol_hint("Where is `create_app` defined?") == "create_app"


def test_symbol_hint_prefers_snake_case():
    assert extract_symbol_hint("Explain create_app factory flow") == "create_app"


def test_embed_prefix_includes_file_and_symbol():
    text = build_embed_text(
        "flask/app.py",
        "def create_app():\n    pass\n",
        method_name="create_app",
        language="python",
    )
    assert "File: flask/app.py" in text
    assert "Symbol: create_app" in text
    assert "Language: python" in text
    assert "def create_app" in text


def test_chunk_tsv_includes_path_and_symbol():
    tsv = build_chunk_tsv(
        "flask/app.py",
        "def create_app(): pass",
        method_name="create_app",
        language="python",
    )
    assert "flask/app.py" in tsv
    assert "create_app" in tsv


def test_is_grounded_requires_citation():
    chunks = [
        {
            "file": "src/auth/service.py",
            "start_line": 10,
            "end_line": 42,
            "content": "def login(): ...",
        }
    ]
    assert is_grounded("Authentication is somewhere in the auth module.", chunks) is False
    assert is_grounded("Auth is in src/auth/service.py:10-42", chunks) is True


def test_footer_alone_is_not_grounded_success():
    """Appending a Sources footer without an inline citation is not success."""
    chunks = [
        {
            "file": "src/auth/service.py",
            "start_line": 10,
            "end_line": 20,
            "content": "code",
        }
    ]
    footer_only = (
        "Auth lives in the service layer.\n\nSources:\n"
        "- src/auth/service.py:10-20"
    )
    assert is_grounded(footer_only, chunks) is False
    refusal = ungrounded_refusal(chunks)
    assert "candidates" in refusal.lower()
    assert is_grounded(refusal, chunks) is True


def test_ungrounded_without_citation_fails():
    chunks = [{"file": "a.py", "start_line": 1, "end_line": 2, "content": "x"}]
    assert is_grounded("Something happens in the codebase.", chunks) is False


def test_rewrite_fallback_produces_extra_terms(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    try:
        result = rewrite_query("Where is the application factory defined?")
        assert "rewrites" in result
        assert len(result["rewrites"]) >= 1
        joined = " ".join(result["rewrites"]).lower()
        assert "application" in joined or "factory" in joined
    finally:
        get_settings.cache_clear()


def test_eval_questions_contract():
    path = Path(__file__).parent / "eval_questions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) >= 10
    for item in data:
        assert "question" in item
        assert "must_cite_path_substr" in item
        assert "must_not_cite" in item
        assert isinstance(item["must_not_cite"], list)


@pytest.mark.skipif(
    not __import__("os").environ.get("RUN_RAG_EVAL"),
    reason="Set RUN_RAG_EVAL=1 with indexed repo to run live retrieval eval",
)
def test_live_rag_eval_placeholder():
    """Optional live eval hook — skipped unless RUN_RAG_EVAL=1."""
    path = Path(__file__).parent / "eval_questions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data  # live wiring left for operators with a running DB
