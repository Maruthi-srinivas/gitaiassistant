from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import FileRecord, KnowledgeNode, Repository, Symbol


def rebuild_knowledge_tree(db: Session, repository_id: uuid.UUID) -> None:
    db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == repository_id).delete()
    db.flush()

    repo = db.get(Repository, repository_id)
    root = KnowledgeNode(
        repository_id=repository_id,
        parent_id=None,
        name=repo.name if repo else "Repository",
        type="repository",
        description="Repository root",
        path="",
    )
    db.add(root)
    db.flush()

    files = db.query(FileRecord).filter(FileRecord.repository_id == repository_id).all()
    dir_nodes: dict[str, KnowledgeNode] = {"": root}

    def ensure_dir(path: str) -> KnowledgeNode:
        if path in dir_nodes:
            return dir_nodes[path]
        parent_path = "/".join(path.split("/")[:-1])
        parent = ensure_dir(parent_path) if path else root
        node = KnowledgeNode(
            repository_id=repository_id,
            parent_id=parent.id,
            name=path.split("/")[-1] if path else "root",
            type="directory",
            path=path,
        )
        db.add(node)
        db.flush()
        dir_nodes[path] = node
        return node

    for f in files:
        parts = f.path.split("/")
        parent_dir = "/".join(parts[:-1])
        parent = ensure_dir(parent_dir)
        file_node = KnowledgeNode(
            repository_id=repository_id,
            parent_id=parent.id,
            name=parts[-1],
            type="file",
            path=f.path,
            description=f.language,
        )
        db.add(file_node)
        db.flush()

        symbols = db.query(Symbol).filter(Symbol.file_id == f.id).all()
        for sym in symbols:
            if sym.type not in {"class", "function", "method"}:
                continue
            db.add(
                KnowledgeNode(
                    repository_id=repository_id,
                    parent_id=file_node.id,
                    name=sym.name,
                    type=sym.type,
                    path=f.path,
                    symbol_id=sym.id,
                    description=f"{sym.type} at lines {sym.start_line}-{sym.end_line}",
                )
            )
    db.flush()


def get_knowledge_tree(db: Session, repository_id: uuid.UUID) -> list[dict]:
    nodes = db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == repository_id).all()
    by_parent: dict[uuid.UUID | None, list[KnowledgeNode]] = defaultdict(list)
    for n in nodes:
        by_parent[n.parent_id].append(n)

    def build(parent_id: uuid.UUID | None) -> list[dict]:
        children = sorted(by_parent.get(parent_id, []), key=lambda x: (x.type, x.name.lower()))
        return [
            {
                "id": str(n.id),
                "name": n.name,
                "type": n.type,
                "description": n.description,
                "path": n.path,
                "children": build(n.id),
            }
            for n in children
        ]

    roots = build(None)
    return roots
