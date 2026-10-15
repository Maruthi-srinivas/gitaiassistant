from __future__ import annotations

import contextvars
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import UsageLog, User

_usage_user: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar("usage_user", default=None)


def set_usage_user(user_id: uuid.UUID | None) -> contextvars.Token:
    return _usage_user.set(user_id)


def reset_usage_user(token: contextvars.Token) -> None:
    _usage_user.reset(token)


def current_usage_user() -> uuid.UUID | None:
    return _usage_user.get()


def record_usage(
    db: Session,
    user_id: uuid.UUID,
    kind: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    db.add(
        UsageLog(
            user_id=user_id,
            kind=kind,
            model=model or "unknown",
            prompt_tokens=max(0, int(prompt_tokens or 0)),
            completion_tokens=max(0, int(completion_tokens or 0)),
        )
    )
    db.flush()


def _budgets_for_plan(plan: str | None) -> tuple[int, int]:
    settings = get_settings()
    if (plan or "free").strip().lower() == "pro":
        return settings.pro_daily_token_budget, settings.pro_monthly_token_budget
    return settings.free_daily_token_budget, settings.free_monthly_token_budget


def tokens_used_since(db: Session, user_id: uuid.UUID, since: datetime) -> int:
    total = (
        db.query(func.coalesce(func.sum(UsageLog.prompt_tokens + UsageLog.completion_tokens), 0))
        .filter(UsageLog.user_id == user_id, UsageLog.created_at >= since)
        .scalar()
    )
    return int(total or 0)


def assert_token_budget(db: Session, user: User) -> None:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    daily_cap, monthly_cap = _budgets_for_plan(user.plan)
    daily_used = tokens_used_since(db, user.id, day_start)
    monthly_used = tokens_used_since(db, user.id, month_start)
    if daily_used >= daily_cap or monthly_used >= monthly_cap:
        raise HTTPException(status_code=429, detail="Token budget exceeded")
