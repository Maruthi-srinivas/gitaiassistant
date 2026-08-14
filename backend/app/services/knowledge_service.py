from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import FileRecord, KnowledgeNode, Repository, Symbol
from app.rag.embeddings import chat_completion

logger = logging.getLogger(__name__)


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


def enrich_knowledge_descriptions(
    db: Session,
    repository_id: uuid.UUID,
    file_paths: list[str] | None = None,
    max_nodes: int = 40,
) -> int:
    """LLM-enrich directory and class nodes with short responsibility blurbs."""
    settings = get_settings()
    if not settings.llm_api_key:
        logger.info("Skipping knowledge enrichment (no LLM_API_KEY)")
        return 0

    q = db.query(KnowledgeNode).filter(
        KnowledgeNode.repository_id == repository_id,
        KnowledgeNode.type.in_(["directory", "class"]),
    )
    if file_paths:
        prefixes: set[str] = set()
        for p in file_paths:
            parts = p.split("/")
            for i in range(len(parts)):
                prefixes.add("/".join(parts[: i + 1]))
            prefixes.add(p)
        nodes = [
            n
            for n in q.all()
            if n.path
            and (
                n.path in prefixes
                or any(
                    fp.startswith(n.path.rstrip("/") + "/") or fp == n.path for fp in file_paths
                )
            )
        ][:max_nodes]
        if not nodes:
            nodes = q.limit(max_nodes).all()
    else:
        nodes = q.limit(max_nodes).all()

    if not nodes:
        return 0

    # Build child name context
    all_nodes = db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == repository_id).all()
    children_by_parent: dict[uuid.UUID | None, list[str]] = defaultdict(list)
    for n in all_nodes:
        if n.parent_id:
            children_by_parent[n.parent_id].append(n.name)

    batch: list[dict] = []
    for n in nodes:
        kids = children_by_parent.get(n.id, [])[:8]
        batch.append(
            {
                "id": str(n.id),
                "name": n.name,
                "type": n.type,
                "path": n.path,
                "children": kids,
            }
        )

    prompt = [
        {
            "role": "system",
            "content": (
                "You describe software repository structure. "
                "For each node return a JSON array of objects with keys id and description "
                "(one concise sentence about responsibility). No markdown."
            ),
        },
        {
            "role": "user",
            "content": "Nodes:\n" + json.dumps(batch),
        },
    ]
    try:
        raw = chat_completion(prompt, temperature=0.2)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        if not isinstance(data, list):
            return 0
        by_id = {str(n.id): n for n in nodes}
        updated = 0
        for item in data:
            nid = str(item.get("id", ""))
            desc = (item.get("description") or "").strip()
            if nid in by_id and desc:
                by_id[nid].description = desc[:500]
                updated += 1
        db.flush()
        return updated
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich_knowledge_descriptions failed: %s", exc)
        return 0


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
