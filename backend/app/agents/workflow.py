from __future__ import annotations

import logging
import operator
import re
import time
import uuid
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agents import tools as agent_tools
from app.agents.query_utils import (
    CITATION_RE,
    citations_match,
    classify_query,
    ensure_citations_footer,
)
from app.models import Conversation, Message, Repository
from app.rag.embeddings import chat_completion
from app.rag.prompts import build_answer_messages, build_source_check_messages

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    repository_id: str
    question: str
    kinds: list
    chunks: Annotated[list, operator.add]
    notes: Annotated[list, operator.add]
    answer: str
    sources: list
    citation_ok: bool


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
    return {"kinds": classify_query(state["question"])}


def node_retrieve(state: AgentState) -> dict:
    """Fan-in: always RAG; also CODE/GRAPH/HISTORY/DOCS when classified."""
    db = _db()
    t0 = time.perf_counter()
    try:
        repo_id = uuid.UUID(state["repository_id"])
        q = state["question"]
        kinds = set(state.get("kinds") or ["RAG"])
        chunks: list[dict] = []
        notes: list[str] = []
        hint = _extract_symbol_hint(q)

        # Always RAG
        chunks.extend(agent_tools.search_code(db, repo_id, q, limit=6))

        if "CODE" in kinds:
            if hint:
                chunks.extend(agent_tools.search_symbol(db, repo_id, hint, limit=8))
                refs = agent_tools.find_references(db, repo_id, hint)
                notes.append(f"references:{refs[:20]}")

        if "GRAPH" in kinds:
            name = hint or "main"
            deps = agent_tools.find_dependencies(db, repo_id, name)
            refs = agent_tools.find_dependents(db, repo_id, name)
            neighbors = agent_tools.get_graph_path(db, repo_id, [name], hops=2)
            notes.append(f"dependencies:{deps[:20]}")
            notes.append(f"dependents:{refs[:20]}")
            notes.append(f"neighbors:{neighbors[:30]}")
            agent_tools.tool_knowledge_tree(db, repo_id)
            chunks.extend(agent_tools.search_symbol(db, repo_id, name, limit=6))

        if "HISTORY" in kinds:
            repo = db.get(Repository, repo_id)
            hist = agent_tools.get_git_history(
                db, repo_id, q, local_path=repo.local_path if repo else None
            )
            chunks.extend(hist)

        if "DOCS" in kinds:
            chunks.extend(agent_tools.tool_search_documentation(db, repo_id, q, limit=6))

        retrieve_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "retrieve repo=%s kinds=%s chunks=%s ms=%s",
            repo_id,
            sorted(kinds),
            len(chunks),
            retrieve_ms,
        )
        return {"chunks": chunks, "notes": notes}
    finally:
        db.close()


def node_answer(state: AgentState) -> dict:
    dedup: dict[str, dict] = {}
    for c in state.get("chunks") or []:
        if "file" not in c:
            continue
        key = f"{c['file']}:{c.get('start_line')}:{c.get('end_line')}"
        dedup.setdefault(key, c)
    context_chunks = list(dedup.values())[:10]
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
            "citation_ok": False,
        }
    messages = build_answer_messages(state["question"], context_chunks)
    t0 = time.perf_counter()
    try:
        answer = chat_completion(messages)
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM call failed")
        top = context_chunks[0]
        answer = (
            f"Relevant code appears in {top['file']}:{top['start_line']}-{top['end_line']}.\n\n"
            f"```\n{top['content'][:1200]}\n```\n\n(LLM unavailable: {exc})"
        )
    llm_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("llm_answer ms=%s", llm_ms)
    sources = [
        {
            "file": c["file"],
            "start_line": c.get("start_line", 1),
            "end_line": c.get("end_line", 1),
            "snippet": (c.get("content") or "")[:400],
        }
        for c in context_chunks
        if c.get("file") not in {"_agent_notes"}
    ]
    return {"answer": answer, "sources": sources}


def node_source_check(state: AgentState) -> dict:
    answer = state.get("answer") or ""
    chunks = [
        c
        for c in (state.get("chunks") or [])
        if c.get("file") and c.get("file") not in {"_agent_notes"}
    ]
    for s in state.get("sources") or []:
        chunks.append(
            {
                "file": s.get("file"),
                "start_line": s.get("start_line", 1),
                "end_line": s.get("end_line", 1),
                "content": s.get("snippet") or "",
            }
        )

    ok = citations_match(answer, chunks)
    if not ok and any(
        str(c.get("file", "")).startswith("commit:") or str(c.get("file", "")).startswith("_git")
        for c in chunks
    ):
        if re.search(r"commit:[0-9a-fA-F]{7,40}", answer) or CITATION_RE.search(answer):
            ok = True

    if ok:
        logger.info("citation_ok=true")
        return {"citation_ok": True}

    logger.info("citation_ok=false; retrying with stricter prompt")
    try:
        messages = build_source_check_messages(state["question"], answer, chunks[:12])
        revised = chat_completion(messages)
        if revised.strip():
            answer = revised
        ok = citations_match(answer, chunks) or bool(CITATION_RE.search(answer))
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_check retry failed: %s", exc)
        answer = ensure_citations_footer(answer, chunks)
        ok = True

    if not ok:
        answer = ensure_citations_footer(answer, chunks)
        ok = True

    return {"answer": answer, "citation_ok": ok}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("classify", node_classify)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("compose_answer", node_answer)
    graph.add_node("source_check", node_source_check)
    graph.set_entry_point("classify")
    graph.add_edge("classify", "retrieve")
    graph.add_edge("retrieve", "compose_answer")
    graph.add_edge("compose_answer", "source_check")
    graph.add_edge("source_check", END)
    return graph.compile()


_GRAPH = None


def get_graph_app():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def run_agent(db: Session, repository_id: uuid.UUID, question: str) -> tuple[str, list[dict]]:
    app = get_graph_app()
    result = app.invoke(
        {
            "repository_id": str(repository_id),
            "question": question,
            "kinds": [],
            "chunks": [],
            "notes": [],
            "answer": "",
            "sources": [],
            "citation_ok": False,
        }
    )
    logger.info(
        "agent done citation_ok=%s sources=%s",
        result.get("citation_ok"),
        len(result.get("sources") or []),
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
