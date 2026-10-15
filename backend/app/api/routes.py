from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    revoke_access_token,
    validate_password_strength,
    verify_password,
)
from app.config import get_settings
from app.crypto import encrypt_secret
from app.database import get_db
from app.models import Dependency, FileRecord, Repository, RepositoryBranch, Symbol, User
from app.schemas import (
    BranchOut,
    ChatRequest,
    ChatResponse,
    ChurnOut,
    CommitOut,
    DependencyOut,
    FileDetailOut,
    FileOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphOut,
    IndexJobOut,
    IndexRequest,
    IndexStatusOut,
    LoginRequest,
    RegisterRequest,
    RepoCreate,
    RepoOut,
    SourceRef,
    SymbolOut,
    TokenResponse,
    UserOut,
    UserUpdate,
)
from app.agents.workflow import chat as agent_chat
from app.agents.workflow import format_sse, iter_chat_events
from app.redis_client import get_redis
from app.services.cache import cache_get, cache_set, chat_cache_key
from app.services.file_discovery import load_file_content, repo_local_path
from app.services.git_history_service import (
    commits_for_path,
    compare_commits,
    file_churn_top,
    module_churn,
)
from app.services.graph_service import get_graph
from app.services.indexing_pipeline import (
    format_index_sse,
    index_progress_channel,
    index_progress_payload,
)
from app.services.knowledge_service import get_knowledge_tree
from app.services.repository_service import (
    assert_index_capacity,
    check_rate_limit,
    create_repository,
    enqueue_index,
    get_repository,
    latest_job,
    list_repositories,
)
from app.services.usage_service import assert_token_budget, reset_usage_user, set_usage_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    check_rate_limit(f"auth:{_client_ip(request)}", settings.auth_rate_limit_per_minute)
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id, user.email))


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    check_rate_limit(f"auth:{_client_ip(request)}", settings.auth_rate_limit_per_minute)
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id, user.email))


@router.post("/auth/logout")
def logout(
    creds: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    _user: User = Depends(get_current_user),
):
    if creds and creds.credentials:
        revoke_access_token(creds.credentials)
    return {"status": "logged_out"}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email, has_github_token=bool(user.github_token))


@router.patch("/auth/me", response_model=UserOut)
def update_me(body: UserUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.github_token is not None:
        raw_token = body.github_token.strip()
        user.github_token = encrypt_secret(raw_token) if raw_token else None
        db.commit()
        db.refresh(user)
    return UserOut(id=user.id, email=user.email, has_github_token=bool(user.github_token))


@router.post("/repositories", response_model=RepoOut, status_code=201)
def create_repo(
    data: RepoCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    try:
        repo = create_repository(db, data.url, owner=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to create repository")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return repo


@router.get("/repositories", response_model=list[RepoOut])
def list_repos(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return list_repositories(db, user_id=user.id)


@router.get("/repositories/{repo_id}", response_model=RepoOut)
def get_repo(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return get_repository(db, repo_id, user_id=user.id)


@router.post("/repositories/{repo_id}/index", response_model=IndexJobOut)
def index_repo(
    repo_id: uuid.UUID,
    body: IndexRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    check_rate_limit(f"index:{user.id}", settings.index_rate_limit_per_minute)
    get_repository(db, repo_id, user_id=user.id)
    assert_index_capacity(db, user.id)
    incremental = body.incremental if body else False
    branch = body.branch if body else None
    job = enqueue_index(db, repo_id, incremental=incremental, branch=branch)
    return IndexJobOut(
        job_id=job.id,
        status=job.status.value,
        progress=job.progress,
        error=job.error,
        incremental=job.incremental,
        branch=job.branch,
        timings=job.timings,
        metrics=job.metrics,
    )


@router.get("/repositories/{repo_id}/index-status", response_model=IndexStatusOut)
def index_status(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    repo = get_repository(db, repo_id, user_id=user.id)
    job = latest_job(db, repo_id)
    return IndexStatusOut(
        repository_id=repo.id,
        status=repo.status.value,
        progress=job.progress if job else 0.0,
        error=repo.error,
        branch=job.branch if job else repo.default_branch,
        timings=job.timings if job else None,
        metrics=job.metrics if job else None,
        job=(
            IndexJobOut(
                job_id=job.id,
                status=job.status.value,
                progress=job.progress,
                error=job.error,
                incremental=job.incremental,
                branch=job.branch,
                timings=job.timings,
                metrics=job.metrics,
            )
            if job
            else None
        ),
    )


def _iter_index_stream(repo: Repository, job):
    payload = index_progress_payload(repo, job)
    yield format_index_sse(payload)
    if payload["status"] in {"COMPLETED", "FAILED"}:
        return
    pubsub = get_redis().pubsub()
    pubsub.subscribe(index_progress_channel(repo.id))
    try:
        while True:
            message = pubsub.get_message(timeout=1.0)
            if not message or message.get("type") != "message":
                continue
            raw = message.get("data")
            text = raw if isinstance(raw, str) else raw.decode("utf-8")
            yield f"data: {text}\n\n"
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                continue
            if body.get("status") in {"COMPLETED", "FAILED"}:
                break
    finally:
        pubsub.close()


@router.get("/repositories/{repo_id}/index-stream")
def index_stream(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    repo = get_repository(db, repo_id, user_id=user.id)
    job = latest_job(db, repo_id)
    return StreamingResponse(
        _iter_index_stream(repo, job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/repositories/{repo_id}/branches", response_model=list[BranchOut])
def list_branches(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    rows = (
        db.query(RepositoryBranch)
        .filter(RepositoryBranch.repository_id == repo_id)
        .order_by(RepositoryBranch.name)
        .all()
    )
    return [
        BranchOut(name=r.name, commit_hash=r.commit_hash, is_indexed=r.is_indexed) for r in rows
    ]


@router.get("/repositories/{repo_id}/history", response_model=list[CommitOut])
def history_for_path(
    repo_id: uuid.UUID,
    path: str = Query("", description="File path or symbol hint"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_repository(db, repo_id, user_id=user.id)
    return [CommitOut(**c) for c in commits_for_path(db, repo_id, path, limit=limit)]


@router.get("/repositories/{repo_id}/history/churn", response_model=list[ChurnOut])
def history_churn(
    repo_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    by: str = Query("module"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_repository(db, repo_id, user_id=user.id)
    if by not in {"module", "file"}:
        raise HTTPException(status_code=400, detail="by must be 'module' or 'file'")
    if by == "file":
        return [
            ChurnOut(
                path=r["path"],
                change_count=r["change_count"],
                last_commit_sha=r.get("last_commit_sha"),
            )
            for r in file_churn_top(db, repo_id, limit=limit)
        ]
    return [
        ChurnOut(module=r["module"], change_count=r["change_count"])
        for r in module_churn(db, repo_id, limit=limit)
    ]


@router.get("/repositories/{repo_id}/history/compare")
def history_compare(
    repo_id: uuid.UUID,
    from_sha: str = Query(..., alias="from"),
    to_sha: str = Query(..., alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = get_repository(db, repo_id, user_id=user.id)
    dest = Path(repo.local_path) if repo.local_path else repo_local_path(str(repo.id))
    return compare_commits(db, repo_id, dest if dest.exists() else None, from_sha, to_sha)


@router.get("/repositories/{repo_id}/tree")
def repo_tree(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    return get_knowledge_tree(db, repo_id)


@router.get("/repositories/{repo_id}/graph", response_model=GraphOut)
def repo_graph(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    nodes, edges = get_graph(db, repo_id)
    return GraphOut(
        nodes=[GraphNodeOut(id=n.id, type=n.type, name=n.name, file_id=n.file_id) for n in nodes],
        edges=[
            GraphEdgeOut(
                id=e.id,
                source_node_id=e.source_node_id,
                target_node_id=e.target_node_id,
                type=e.type,
            )
            for e in edges
        ],
    )


@router.get("/repositories/{repo_id}/files", response_model=list[FileOut])
def list_files(
    repo_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    return db.query(FileRecord).filter(FileRecord.repository_id == repo_id).order_by(FileRecord.path).all()


@router.get("/repositories/{repo_id}/files/{file_id}", response_model=FileDetailOut)
def get_file(
    repo_id: uuid.UUID, file_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    repo = get_repository(db, repo_id, user_id=user.id)
    f = db.get(FileRecord, file_id)
    if not f or f.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="File not found")
    return FileDetailOut(
        id=f.id,
        path=f.path,
        language=f.language,
        size=f.size,
        content=load_file_content(f, repo.local_path),
    )


@router.get("/repositories/{repo_id}/symbols/{symbol}", response_model=list[SymbolOut])
def get_symbols(
    repo_id: uuid.UUID, symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    rows = (
        db.query(Symbol, FileRecord.path)
        .join(FileRecord, FileRecord.id == Symbol.file_id)
        .filter(FileRecord.repository_id == repo_id, Symbol.name.ilike(f"%{symbol}%"))
        .limit(50)
        .all()
    )
    return [
        SymbolOut(
            id=s.id,
            name=s.name,
            type=s.type,
            start_line=s.start_line,
            end_line=s.end_line,
            file_path=path,
            signature=s.signature,
        )
        for s, path in rows
    ]


@router.get("/repositories/{repo_id}/dependencies/{symbol}", response_model=list[DependencyOut])
def get_dependencies(
    repo_id: uuid.UUID, symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    rows = (
        db.query(Dependency)
        .filter(Dependency.repository_id == repo_id, Dependency.source_name.ilike(f"%{symbol}%"))
        .limit(100)
        .all()
    )
    return [DependencyOut(source_name=d.source_name, target_name=d.target_name, type=d.type) for d in rows]


@router.get("/repositories/{repo_id}/references/{symbol}", response_model=list[DependencyOut])
def get_references(
    repo_id: uuid.UUID, symbol: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    get_repository(db, repo_id, user_id=user.id)
    rows = (
        db.query(Dependency)
        .filter(Dependency.repository_id == repo_id, Dependency.target_name.ilike(f"%{symbol}%"))
        .limit(100)
        .all()
    )
    return [DependencyOut(source_name=d.source_name, target_name=d.target_name, type=d.type) for d in rows]


@router.post("/repositories/{repo_id}/chat", response_model=ChatResponse)
def chat_repo(
    repo_id: uuid.UUID,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    check_rate_limit(f"chat:{user.id}", settings.chat_rate_limit_per_minute)
    repo = get_repository(db, repo_id, user_id=user.id)
    assert_token_budget(db, user)
    cache_key = chat_cache_key(str(repo_id), body.message, repo.commit_hash)

    if body.conversation_id is None:
        cached = cache_get(cache_key)
        if cached:
            return ChatResponse(**cached)

    usage_ctx = set_usage_user(user.id)
    try:
        conversation, answer, sources = agent_chat(
            db, repo_id, body.message, conversation_id=body.conversation_id, user_id=user.id
        )
    finally:
        reset_usage_user(usage_ctx)
    response = ChatResponse(
        conversation_id=conversation.id,
        answer=answer,
        sources=[SourceRef(**s) for s in sources],
    )
    if body.conversation_id is None:
        cache_set(cache_key, response.model_dump(mode="json"))
    return response


@router.post("/repositories/{repo_id}/chat/stream")
def chat_stream(
    repo_id: uuid.UUID,
    body: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    check_rate_limit(f"chat:{user.id}", settings.chat_rate_limit_per_minute)
    get_repository(db, repo_id, user_id=user.id)
    assert_token_budget(db, user)

    def generate():
        for kind, payload in iter_chat_events(
            db,
            repo_id,
            body.message,
            conversation_id=body.conversation_id,
            user_id=user.id,
        ):
            if kind == "done":
                yield format_sse("done", json.dumps(payload))
            else:
                yield format_sse(kind, str(payload))

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
