from __future__ import annotations

import logging
import operator
import re
import uuid
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.models import Conversation, Dependency, FileRecord, Message
from app.rag.embeddings import chat_completion
from app.rag.prompts import build_answer_messages
from app.rag.retriever import hybrid_retrieve, symbol_search
from app.services.graph_service import expand_graph_neighbors
from app.services.knowledge_service import get_knowledge_tree

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    repository_id: str
    question: str
    kind: str
    chunks: Annotated[list, operator.add]
    notes: Annotated[list, operator.add]
    answer: str
    sources: list


def classify_query(question: str) -> str:
    q = question.lower()
    if any(k in q for k in ("depend", "call", "import", "extends", "who uses", "graph", "path")):
        return "GRAPH"
    if any(k in q for k in ("where is", "defined", "definition", "symbol", "function", "class", "method")):
        return "CODE"
    return "RAG"


def _extract_symbol_hint(question: str) -> str | None:
    m = re.search(r"`([^`]+)`", question)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", question)
    return m.group(1) if m else None


def _db() -> Session:
    from app.database import SessionLocal

    return SessionLocal()


def node_classify(state: AgentState) -> dict:
    return {"kind": classify_query(state["question"])}


def node_code_query(state: AgentState) -> dict:
    db = _db()
    try:
        repo_id = uuid.UUID(state["repository_id"])
        q = state["question"]
        hint = _extract_symbol_hint(q)
        chunks = []
        if hint:
            chunks.extend(symbol_search(db, repo_id, hint, limit=8))
            deps = (
                db.query(Dependency)
                .filter(Dependency.repository_id == repo_id, Dependency.target_name.ilike(f"%{hint}%"))
                .limit(30)
                .all()
            )
            notes = [f"references:{[{'s': d.source_name, 't': d.target_name, 'type': d.type} for d in deps]}"]
        else:
            notes = []
        chunks.extend(hybrid_retrieve(db, repo_id, q, limit=6))
        return {"chunks": chunks, "notes": notes}
    finally:
        db.close()


def node_graph_query(state: AgentState) -> dict:
    db = _db()
    try:
        repo_id = uuid.UUID(state["repository_id"])
        q = state["question"]
        hint = _extract_symbol_hint(q) or "main"
        deps = (
            db.query(Dependency)
            .filter(Dependency.repository_id == repo_id, Dependency.source_name.ilike(f"%{hint}%"))
            .limit(30)
            .all()
        )
        refs = (
            db.query(Dependency)
            .filter(Dependency.repository_id == repo_id, Dependency.target_name.ilike(f"%{hint}%"))
            .limit(30)
            .all()
        )
        neighbors = expand_graph_neighbors(db, repo_id, [hint], hops=2)
        notes = [
            f"dependencies:{[{'s': d.source_name, 't': d.target_name, 'type': d.type} for d in deps]}",
            f"dependents:{[{'s': d.source_name, 't': d.target_name, 'type': d.type} for d in refs]}",
            f"neighbors:{neighbors}",
        ]
        chunks = symbol_search(db, repo_id, hint, limit=8)
        chunks.extend(hybrid_retrieve(db, repo_id, q, limit=6))
        return {"chunks": chunks, "notes": notes}
    finally:
        db.close()


def node_rag_query(state: AgentState) -> dict:
    db = _db()
    try:
        repo_id = uuid.UUID(state["repository_id"])
        chunks = hybrid_retrieve(db, repo_id, state["question"], limit=8)
        return {"chunks": chunks, "notes": []}
    finally:
        db.close()


def route_after_classify(state: AgentState) -> str:
    return state["kind"]


def node_answer(state: AgentState) -> dict:
    dedup: dict[str, dict] = {}
    for c in state.get("chunks") or []:
        if "file" not in c:
            continue
        key = f"{c['file']}:{c.get('start_line')}:{c.get('end_line')}"
        dedup.setdefault(key, c)
    context_chunks = list(dedup.values())[:8]
    notes = state.get("notes") or []
    if notes:
        context_chunks.append(
            {
                "file": "_agent_notes",
                "start_line": 1,
                "end_line": 1,
                "content": "\n".join(str(n) for n in notes),
            }
        )
    if not context_chunks:
        return {
            "answer": "I could not find relevant code context for that question. Index the repository first or rephrase.",
            "sources": [],
        }
    messages = build_answer_messages(state["question"], context_chunks)
    try:
        answer = chat_completion(messages)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM call failed")
        top = context_chunks[0]
        answer = (
            f"Relevant code appears in {top['file']}:{top['start_line']}-{top['end_line']}.\n\n"
            f"```\n{top['content'][:1200]}\n```\n\n(LLM unavailable: {exc})"
        )
    sources = [
        {
            "file": c["file"],
            "start_line": c.get("start_line", 1),
            "end_line": c.get("end_line", 1),
            "snippet": (c.get("content") or "")[:400],
        }
        for c in context_chunks
        if c.get("file") != "_agent_notes"
    ]
    return {"answer": answer, "sources": sources}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("classify", node_classify)
    graph.add_node("CODE", node_code_query)
    graph.add_node("GRAPH", node_graph_query)
    graph.add_node("RAG", node_rag_query)
    graph.add_node("compose_answer", node_answer)
    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {"CODE": "CODE", "GRAPH": "GRAPH", "RAG": "RAG"},
    )
    graph.add_edge("CODE", "compose_answer")
    graph.add_edge("GRAPH", "compose_answer")
    graph.add_edge("RAG", "compose_answer")
    graph.add_edge("compose_answer", END)
    return graph.compile()


_GRAPH = None


def get_graph_app():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def run_agent(db: Session, repository_id: uuid.UUID, question: str) -> tuple[str, list[dict]]:
    # Touch knowledge tree tool for graph-oriented questions (side observability)
    if classify_query(question) == "GRAPH":
        get_knowledge_tree(db, repository_id)
    app = get_graph_app()
    result = app.invoke(
        {
            "repository_id": str(repository_id),
            "question": question,
            "kind": "",
            "chunks": [],
            "notes": [],
            "answer": "",
            "sources": [],
        }
    )
    return result.get("answer") or "", result.get("sources") or []


def chat(
    db: Session,
    repository_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None = None,
) -> tuple[Conversation, str, list[dict]]:
    conversation = None
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if not conversation or conversation.repository_id != repository_id:
            conversation = None
    if not conversation:
        conversation = Conversation(repository_id=repository_id)
        db.add(conversation)
        db.flush()

    db.add(Message(conversation_id=conversation.id, role="user", content=message))
    answer, sources = run_agent(db, repository_id, message)
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            sources=sources,
        )
    )
    db.commit()
    db.refresh(conversation)
    return conversation, answer, sources
