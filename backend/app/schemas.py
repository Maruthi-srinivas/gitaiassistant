from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RepoCreate(BaseModel):
    url: str


class RepoOut(BaseModel):
    id: uuid.UUID
    github_url: str
    owner: str
    name: str
    default_branch: str | None = None
    commit_hash: str | None = None
    status: str
    error: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class IndexRequest(BaseModel):
    incremental: bool = False
    branch: str | None = None


class IndexJobOut(BaseModel):
    job_id: uuid.UUID
    status: str
    progress: float = 0.0
    error: str | None = None
    incremental: bool = False
    branch: str | None = None
    timings: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class IndexStatusOut(BaseModel):
    repository_id: uuid.UUID
    status: str
    job: IndexJobOut | None = None
    progress: float = 0.0
    error: str | None = None
    branch: str | None = None
    timings: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None


class BranchOut(BaseModel):
    name: str
    commit_hash: str | None = None
    is_indexed: bool = False


class CommitOut(BaseModel):
    sha: str
    author: str | None = None
    authored_at: str | None = None
    message: str | None = None
    path: str | None = None
    change_type: str | None = None


class ChurnOut(BaseModel):
    module: str | None = None
    path: str | None = None
    change_count: int
    last_commit_sha: str | None = None


class FileOut(BaseModel):
    id: uuid.UUID
    path: str
    language: str | None = None
    size: int = 0

    model_config = {"from_attributes": True}


class FileDetailOut(FileOut):
    content: str | None = None
    hash: str | None = None


class SymbolOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    start_line: int
    end_line: int
    file_path: str | None = None
    signature: str | None = None

    model_config = {"from_attributes": True}


class DependencyOut(BaseModel):
    source_name: str
    target_name: str
    type: str


class GraphNodeOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    file_id: uuid.UUID | None = None


class GraphEdgeOut(BaseModel):
    id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    type: str


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class KnowledgeNodeOut(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    description: str | None = None
    path: str | None = None
    children: list[KnowledgeNodeOut] = Field(default_factory=list)


class SourceRef(BaseModel):
    file: str
    start_line: int
    end_line: int
    snippet: str | None = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    sources: list[SourceRef] = Field(default_factory=list)
