"""V5 baseline schema, pgvector HNSW index, and composite B-tree indexes.

Revision ID: 0001_v5_baseline
Revises:
Create Date: 2026-10-15
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app import models  # noqa: F401
from app.database import Base

revision: str = "0001_v5_baseline"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

UPGRADE_DDL = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "ALTER TABLE indexing_jobs ADD COLUMN IF NOT EXISTS branch VARCHAR(255)",
    "ALTER TABLE indexing_jobs ADD COLUMN IF NOT EXISTS timings JSONB",
    "ALTER TABLE indexing_jobs ADD COLUMN IF NOT EXISTS metrics JSONB",
    "ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS search_tsv tsvector",
    "ALTER TABLE repositories ADD COLUMN IF NOT EXISTS owner_user_id UUID",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id UUID",
    (
        "CREATE INDEX IF NOT EXISTS ix_code_chunks_search_tsv "
        "ON code_chunks USING GIN (search_tsv)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_code_chunks_embedding_hnsw "
        "ON code_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_code_chunks_repo_file "
        "ON code_chunks (repository_id, file_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_symbols_file_name "
        "ON symbols (file_id, name)"
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=bind)
    for stmt in UPGRADE_DDL:
        bind.execute(text(stmt))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("DROP INDEX IF EXISTS ix_symbols_file_name"))
    bind.execute(text("DROP INDEX IF EXISTS ix_code_chunks_repo_file"))
    bind.execute(text("DROP INDEX IF EXISTS ix_code_chunks_embedding_hnsw"))
    bind.execute(text("DROP INDEX IF EXISTS ix_code_chunks_search_tsv"))
