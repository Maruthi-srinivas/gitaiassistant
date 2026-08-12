from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Dependency, FileRecord, GraphEdge, GraphNode, KnowledgeNode, Symbol


def rebuild_graph(db: Session, repository_id: uuid.UUID) -> None:
    db.query(GraphEdge).filter(GraphEdge.repository_id == repository_id).delete()
    db.query(GraphNode).filter(GraphNode.repository_id == repository_id).delete()
    db.flush()

    symbols = (
        db.query(Symbol)
        .join(FileRecord, FileRecord.id == Symbol.file_id)
        .filter(FileRecord.repository_id == repository_id)
        .all()
    )
    node_by_key: dict[str, GraphNode] = {}

    for sym in symbols:
        key = f"symbol:{sym.name}"
        if key in node_by_key:
            continue
        node = GraphNode(
            repository_id=repository_id,
            type=sym.type.upper(),
            name=sym.name,
            file_id=sym.file_id,
            symbol_id=sym.id,
        )
        db.add(node)
        db.flush()
        node_by_key[key] = node
        node_by_key[f"symbol:{sym.name.split('.')[-1]}"] = node

    deps = db.query(Dependency).filter(Dependency.repository_id == repository_id).all()
    for dep in deps:
        src = node_by_key.get(f"symbol:{dep.source_name}")
        tgt = node_by_key.get(f"symbol:{dep.target_name}") or node_by_key.get(
            f"symbol:{dep.target_name.split('.')[-1]}"
        )
        if not src:
            src = GraphNode(
                repository_id=repository_id,
                type="MODULE" if dep.source_name == "<module>" else "SYMBOL",
                name=dep.source_name,
                file_id=dep.file_id,
            )
            db.add(src)
            db.flush()
            node_by_key[f"symbol:{dep.source_name}"] = src
        if not tgt:
            tgt = GraphNode(
                repository_id=repository_id,
                type="EXTERNAL",
                name=dep.target_name,
            )
            db.add(tgt)
            db.flush()
            node_by_key[f"symbol:{dep.target_name}"] = tgt
        db.add(
            GraphEdge(
                repository_id=repository_id,
                source_node_id=src.id,
                target_node_id=tgt.id,
                type=dep.type,
                metadata_json={},
            )
        )
    db.flush()


def get_graph(db: Session, repository_id: uuid.UUID) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes = db.query(GraphNode).filter(GraphNode.repository_id == repository_id).all()
    edges = db.query(GraphEdge).filter(GraphEdge.repository_id == repository_id).all()
    return nodes, edges


def expand_graph_neighbors(
    db: Session, repository_id: uuid.UUID, names: list[str], hops: int = 1
) -> list[str]:
    if not names:
        return []
    nodes = (
        db.query(GraphNode)
        .filter(GraphNode.repository_id == repository_id, GraphNode.name.in_(names))
        .all()
    )
    frontier = {n.id for n in nodes}
    seen_names = set(names)
    for _ in range(hops):
        if not frontier:
            break
        edges = (
            db.query(GraphEdge)
            .filter(
                GraphEdge.repository_id == repository_id,
                (GraphEdge.source_node_id.in_(frontier)) | (GraphEdge.target_node_id.in_(frontier)),
            )
            .all()
        )
        next_ids: set[uuid.UUID] = set()
        for e in edges:
            next_ids.add(e.source_node_id)
            next_ids.add(e.target_node_id)
        neighbors = db.query(GraphNode).filter(GraphNode.id.in_(next_ids)).all()
        for n in neighbors:
            seen_names.add(n.name)
        frontier = next_ids
    return list(seen_names)
