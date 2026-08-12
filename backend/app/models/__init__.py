from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.database import Base

_dims = get_settings().embedding_dimensions


class RepoStatus(str, enum.Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    CLONING = "CLONING"
    ANALYZING = "ANALYZING"
    PARSING = "PARSING"
    GRAPH_BUILDING = "GRAPH_BUILDING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    CLONING = "CLONING"
    ANALYZING = "ANALYZING"
    PARSING = "PARSING"
    GRAPH_BUILDING = "GRAPH_BUILDING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    github_url: Mapped[str] = mapped_column(String(512), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[RepoStatus] = mapped_column(
        Enum(RepoStatus, name="repo_status", values_callable=lambda obj: [e.value for e in obj]),
        default=RepoStatus.CREATED,
    )
    local_path: Mapped[str | None] = mapped_column(String(1024))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    files: Mapped[list["FileRecord"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["IndexingJob"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class IndexingJob(Base):
    __tablename__ = "indexing_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda obj: [e.value for e in obj]),
        default=JobStatus.QUEUED,
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    incremental: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    repository: Mapped[Repository] = relationship(back_populates="jobs")


class FileRecord(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("repository_id", "path", name="uq_file_repo_path"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    language: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    hash: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(Text)

    repository: Mapped[Repository] = relationship(back_populates="files")
    symbols: Mapped[list["Symbol"]] = relationship(back_populates="file", cascade="all, delete-orphan")
    chunks: Mapped[list["CodeChunk"]] = relationship(back_populates="file", cascade="all, delete-orphan")


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_symbol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"))
    signature: Mapped[str | None] = mapped_column(Text)

    file: Mapped[FileRecord] = relationship(back_populates="symbols")


class Dependency(Base):
    __tablename__ = "dependencies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    source_symbol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("symbols.id", ondelete="CASCADE"))
    target_symbol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"))
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    target_name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))


class GraphNode(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"))


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    source_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"))
    target_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"))
    path: Mapped[str | None] = mapped_column(String(1024))


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("symbols.id", ondelete="SET NULL"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str | None] = mapped_column(String(512))
    method_name: Mapped[str | None] = mapped_column(String(512))
    language: Mapped[str | None] = mapped_column(String(64))
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    embedding = mapped_column(Vector(_dims), nullable=True)
    tsv: Mapped[str | None] = mapped_column(Text)

    file: Mapped[FileRecord] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
