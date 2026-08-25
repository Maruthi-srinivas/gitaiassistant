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
    extract_symbol_hint,
    format_history,
    is_grounded,
    rewrite_query,
    ungrounded_refusal,
)
from app.config import get_settings
from app.models import Conversation, Message, Repository
from app.rag.embeddings import chat_completion
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.prompts import build_answer_messages, build_source_check_messages

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    repository_id: str
    question: str
    history: str
    kinds: list
    chunks: Annotated[list, operator.add]
    notes: Annotated[list, operator.add]
    answer: str
    sources: list
    citation_ok: bool


def _db() -> Session:
    from app.database import SessionLocal

    return SessionLocal()


def node_classify(state: AgentState) -> dict:
    return {"kinds": classify_query(state["question"])}


def node_retrieve(state: AgentState) -> dict:
    """Fan-in: always RAG (multi-query); also CODE/GRAPH/HISTORY/DOCS when classified."""
    db = _db()
    t0 = time.perf_counter()
    settings = get_settings()
    try:
        repo_id = uuid.UUID(state["repository_id"])
        q = state["question"]
        history = state.get("history") or ""
        kinds = set(state.get("kinds") or ["RAG"])
        chunks: list[dict] = []
        notes: list[str] = []

        rewritten = rewrite_query(q, history)
        rewrites = rewritten.get("rewrites") or [q]
        symbols = rewritten.get("symbols") or []
        hint = symbols[0] if symbols else extract_symbol_hint(q)
        # Also pull symbols from history for follow-ups like "who calls that?"
        if not hint and history:
            hint = extract_symbol_hint(history)

        # Multi-query hybrid retrieve, then RRF-merge (cap extra embed calls)
        rag_lists: list[list[dict]] = []
        for rq in rewrites[:3]:
            rag_lists.append(agent_tools.search_code(db, repo_id, rq))
        if rag_lists:
            chunks.extend(reciprocal_rank_fusion(rag_lists, limit=settings.context_chunks))

        if "CODE" in kinds:
            for sym in (symbols[:3] if symbols else ([hint] if hint else [])):
                if not sym:
                    continue
                chunks.extend(agent_tools.search_symbol(db, repo_id, sym, limit=8))
                refs = agent_tools.find_references(db, repo_id, sym)
                notes.append(f"references:{sym}:{refs[:20]}")

        if "GRAPH" in kinds:
            name = hint or (symbols[0] if symbols else None)
            if name:
                deps = agent_tools.find_dependencies(db, repo_id, name)
                refs = agent_tools.find_dependents(db, repo_id, name)
                neighbors = agent_tools.get_graph_path(db, repo_id, [name], hops=2)
                notes.append(f"dependencies:{deps[:20]}")
                notes.append(f"dependents:{refs[:20]}")
                notes.append(f"neighbors:{neighbors[:30]}")
                chunks.extend(agent_tools.search_symbol(db, repo_id, name, limit=6))

        if "HISTORY" in kinds:
            repo = db.get(Repository, repo_id)
            hist = agent_tools.get_git_history(
                db, repo_id, q, local_path=repo.local_path if repo else None
            )
            chunks.extend(hist)

        if "DOCS" in kinds:
            for rq in rewrites[:2]:
                chunks.extend(agent_tools.tool_search_documentation(db, repo_id, rq, limit=6))

        retrieve_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "retrieve repo=%s kinds=%s rewrites=%s chunks=%s ms=%s",
            repo_id,
            sorted(kinds),
            len(rewrites),
            len(chunks),
            retrieve_ms,
        )
        return {"chunks": chunks, "notes": notes}
    finally:
        db.close()


def _structured_notes(notes: list[str]) -> str | None:
    """Keep only deps/refs-style notes for the LLM; drop raw dumps."""
    kept = []
    for n in notes:
        s = str(n)
        if s.startswith(("dependencies:", "dependents:", "references:", "neighbors:")):
            kept.append(s)
    if not kept:
        return None
    return "\n".join(kept)


def node_answer(state: AgentState) -> dict:
    settings = get_settings()
    dedup: dict[str, dict] = {}
    for c in state.get("chunks") or []:
        if "file" not in c:
            continue
        key = f"{c['file']}:{c.get('start_line')}:{c.get('end_line')}"
        dedup.setdefault(key, c)
    context_chunks = list(dedup.values())[: settings.context_chunks]

    structured = _structured_notes(state.get("notes") or [])
    if structured:
        context_chunks.append(
            {
                "file": "_agent_notes",
                "start_line": 1,
                "end_line": 1,
                "content": structured,
            }
        )

    if not context_chunks:
        return {
            "answer": (
                "I could not find relevant code context for that question. "
                "Index the repository first or rephrase."
            ),
            "sources": [],
            "citation_ok": False,
        }

    history = state.get("history") or ""
    messages = build_answer_messages(state["question"], context_chunks, history=history)
    t0 = time.perf_counter()
    try:
        answer = chat_completion(messages, temperature=0.0)
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

    ok = is_grounded(answer, chunks)
    if not ok and any(
        str(c.get("file", "")).startswith("commit:") or str(c.get("file", "")).startswith("_git")
        for c in chunks
    ):
        if re.search(r"commit:[0-9a-fA-F]{7,40}", answer) or CITATION_RE.search(answer):
            ok = True

    if ok:
        logger.info("citation_ok=true")
        return {"citation_ok": True, "answer": answer}

    logger.info("citation_ok=false; retrying with stricter prompt")
    try:
        messages = build_source_check_messages(state["question"], answer, chunks[:12])
        revised = chat_completion(messages, temperature=0.0)
        if revised.strip():
            answer = revised
        ok = is_grounded(answer, chunks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_check retry failed: %s", exc)
        ok = False

    if not ok:
        # Do NOT treat a dumped Sources footer as success
        answer = ungrounded_refusal(chunks)
        ok = False
        logger.info("citation_ok=false; returning ungrounded refusal")

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


def run_agent(
    db: Session,
    repository_id: uuid.UUID,
    question: str,
    history: str = "",
) -> tuple[str, list[dict]]:
    app = get_graph_app()
    result = app.invoke(
        {
            "repository_id": str(repository_id),
            "question": question,
            "history": history,
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
    user_id: uuid.UUID | None = None,
) -> tuple[Conversation, str, list[dict]]:
    conversation = None
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if (
            not conversation
            or conversation.repository_id != repository_id
            or (user_id and conversation.user_id and conversation.user_id != user_id)
        ):
            conversation = None
    if not conversation:
        conversation = Conversation(repository_id=repository_id, user_id=user_id)
        db.add(conversation)
        db.flush()

    # Load prior turns before adding the new user message
    prior = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    history = format_history(
        [{"role": m.role, "content": m.content} for m in prior],
        max_turns=6,
    )

    db.add(Message(conversation_id=conversation.id, role="user", content=message))
    answer, sources = run_agent(db, repository_id, message, history=history)
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
