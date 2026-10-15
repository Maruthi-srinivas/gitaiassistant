from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.database import get_alembic_config
from app.models import FileRecord, Symbol
from app.services.cache import chat_cache_key, invalidate_repo_chat_cache
from app.services.file_discovery import load_file_content
from app.services.rag_service import chunk_repository


def test_chat_cache_key_includes_commit_hash():
    repo_id = str(uuid.uuid4())
    k1 = chat_cache_key(repo_id, "How does auth work?", commit_hash="abc1234567890")
    k2 = chat_cache_key(repo_id, "How does auth work?", commit_hash="def9876543210")
    k_none = chat_cache_key(repo_id, "How does auth work?", commit_hash=None)

    assert k1 != k2
    assert k1 != k_none
    assert "abc1234567890" in k1
    assert "def9876543210" in k2


@patch("app.services.cache.get_redis")
def test_invalidate_repo_chat_cache_deletes_matching_keys(mock_get_redis):
    fake_redis = MagicMock()
    repo_id = str(uuid.uuid4())
    keys = [f"cache:chat:{repo_id}:abc:111", f"cache:chat:{repo_id}:def:222"]
    fake_redis.scan_iter.return_value = iter(keys)
    fake_redis.delete.return_value = 2
    mock_get_redis.return_value = fake_redis

    deleted = invalidate_repo_chat_cache(repo_id)
    assert deleted == 2
    fake_redis.scan_iter.assert_called_once_with(match=f"cache:chat:{repo_id}:*")
    fake_redis.delete.assert_called_once_with(*keys)


def test_load_file_content_reads_worktree_and_falls_back(tmp_path: Path):
    repo_id = uuid.uuid4()
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    target_file = src_dir / "app.py"
    target_file.write_text("def hello():\n    return 42\n", encoding="utf-8")

    # Content is None in DB, loaded from worktree
    rec = FileRecord(
        id=uuid.uuid4(),
        repository_id=repo_id,
        path="src/app.py",
        language="python",
        size=27,
        content=None,
    )
    assert "return 42" in load_file_content(rec, local_path=tmp_path)

    # Path traversal outside worktree is rejected and falls back to DB content
    traversal_rec = FileRecord(
        id=uuid.uuid4(),
        repository_id=repo_id,
        path="../../outside.py",
        language="python",
        size=10,
        content="fallback_db_text",
    )
    assert load_file_content(traversal_rec, local_path=tmp_path) == "fallback_db_text"

    # Missing file on disk falls back to DB content
    missing_rec = FileRecord(
        id=uuid.uuid4(),
        repository_id=repo_id,
        path="src/missing.py",
        language="python",
        size=12,
        content="legacy_db_body",
    )
    assert load_file_content(missing_rec, local_path=tmp_path) == "legacy_db_body"


def test_chunk_repository_uses_content_map_when_db_content_is_none():
    repo_id = uuid.uuid4()
    file_id = uuid.uuid4()
    file_rec = FileRecord(
        id=file_id,
        repository_id=repo_id,
        path="pkg/service.py",
        language="python",
        size=45,
        content=None,
    )
    sym = Symbol(
        id=uuid.uuid4(),
        file_id=file_id,
        name="run_task",
        type="function",
        start_line=1,
        end_line=2,
    )

    db = MagicMock()
    file_query = MagicMock()
    file_query.filter.return_value.all.return_value = [file_rec]
    sym_query = MagicMock()
    sym_query.filter.return_value.all.return_value = [sym]
    chunk_del_query = MagicMock()
    chunk_del_query.filter.return_value.delete.return_value = 0

    def query_side_effect(model):
        if model is FileRecord:
            return file_query
        if model is Symbol:
            return sym_query
        return chunk_del_query

    db.query.side_effect = query_side_effect

    count = chunk_repository(
        db,
        repo_id,
        commit_hash="deadbeef",
        content_by_file_id={file_id: "def run_task():\n    return 'ok'\n"},
    )
    assert count == 1
    added_chunk = db.add.call_args[0][0]
    assert "return 'ok'" in added_chunk.content


def test_alembic_baseline_migration_defines_hnsw_and_btree_indexes():
    cfg = get_alembic_config()
    assert Path(cfg.get_main_option("script_location")).exists()

    migration_path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "0001_v5_baseline_and_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0001", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    ddl_joined = "\n".join(module.UPGRADE_DDL)
    assert "ix_code_chunks_embedding_hnsw" in ddl_joined
    assert "USING hnsw (embedding vector_cosine_ops)" in ddl_joined
    assert "ix_code_chunks_repo_file" in ddl_joined
    assert "ix_symbols_file_name" in ddl_joined
