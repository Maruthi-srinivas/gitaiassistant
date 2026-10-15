"""Usage logs and user plan.

Revision ID: 0002_usage_logs
Revises: 0001_v5_baseline
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002_usage_logs"
down_revision: str | None = "0001_v5_baseline"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(32) DEFAULT 'free'"))
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS usage_logs (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind VARCHAR(32) NOT NULL,
                model VARCHAR(128) NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_usage_logs_user_id ON usage_logs (user_id)"))


def downgrade() -> None:
    op.drop_index("ix_usage_logs_user_id", table_name="usage_logs")
    op.drop_table("usage_logs")
    op.drop_column("users", "plan")
