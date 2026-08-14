from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Dependency, FileRecord
from app.rag.retriever import hybrid_retrieve, search_documentation, symbol_search
from app.services.file_discovery import repo_local_path
from app.services.git_history_service import get_git_history_context
from app.services.graph_service import expand_graph_neighbors, get_graph
from app.services.knowledge_service import get_knowledge_tree


def search_code(db: Session, repository_id: uuid.UUID, query: str, limit: int = 8) -> list[dict]:
    return hybrid_retrieve(db, repository_id, query, limit=limit)


def read_file(db: Session, repository_id: uuid.UUID, path: str) -> dict | None:
    row = (
        db.query(FileRecord)
        .filter(FileRecord.repository_id == repository_id, FileRecord.path == path)
        .first()
    )
    if not row:
        # fuzzy
        row = (
            db.query(FileRecord)
            .filter(FileRecord.repository_id == repository_id, FileRecord.path.ilike(f"%{path}%"))
            .first()
        )
    if not row:
        return None
    return {
        "file": row.path,
        "start_line": 1,
        "end_line": max(1, len((row.content or "").splitlines())),
        "content": (row.content or "")[:8000],
        "language": row.language,
        "score": 1.0,
    }


def search_symbol(db: Session, repository_id: uuid.UUID, name: str, limit: int = 8) -> list[dict]:
    return symbol_search(db, repository_id, name, limit=limit)


def find_references(db: Session, repository_id: uuid.UUID, symbol: str, limit: int = 40) -> list[dict]:
    rows = (
        db.query(Dependency)
        .filter(
            Dependency.repository_id == repository_id,
            Dependency.target_name.ilike(f"%{symbol}%"),
        )
        .limit(limit)
        .all()
    )
    return [{"source": d.source_name, "target": d.target_name, "type": d.type} for d in rows]


def find_dependencies(
    db: Session, repository_id: uuid.UUID, symbol: str, limit: int = 40
) -> list[dict]:
    rows = (
        db.query(Dependency)
        .filter(
            Dependency.repository_id == repository_id,
            Dependency.source_name.ilike(f"%{symbol}%"),
        )
        .limit(limit)
        .all()
    )
    return [{"source": d.source_name, "target": d.target_name, "type": d.type} for d in rows]


def find_dependents(
    db: Session, repository_id: uuid.UUID, symbol: str, limit: int = 40
) -> list[dict]:
    return find_references(db, repository_id, symbol, limit=limit)


def get_file_structure(db: Session, repository_id: uuid.UUID, limit: int = 200) -> list[str]:
    rows = (
        db.query(FileRecord.path)
        .filter(FileRecord.repository_id == repository_id)
        .order_by(FileRecord.path)
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def tool_knowledge_tree(db: Session, repository_id: uuid.UUID) -> list[dict]:
    return get_knowledge_tree(db, repository_id)


def get_graph_path(
    db: Session, repository_id: uuid.UUID, names: list[str], hops: int = 2
) -> list[str]:
    return expand_graph_neighbors(db, repository_id, names, hops=hops)


def get_git_history(
    db: Session, repository_id: uuid.UUID, question: str, local_path: str | None = None
) -> list[dict]:
    dest = Path(local_path) if local_path else repo_local_path(str(repository_id))
    return get_git_history_context(
        db, repository_id, question, dest if dest.exists() else None
    )


def tool_search_documentation(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 8
) -> list[dict]:
    return search_documentation(db, repository_id, query, limit=limit)


def graph_summary(db: Session, repository_id: uuid.UUID, limit: int = 30) -> dict:
    nodes, edges = get_graph(db, repository_id)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "sample_nodes": [{"name": n.name, "type": n.type} for n in nodes[:limit]],
        "sample_edges": [
            {"source": str(e.source_node_id), "target": str(e.target_node_id), "type": e.type}
            for e in edges[:limit]
        ],
    }
