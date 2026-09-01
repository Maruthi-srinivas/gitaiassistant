# LangGraph Architecture Deep Dive

**Repository:** GitHub Repository AI Assistant (`BEAIASSISTANT`)  
**LangGraph version:** `0.2.60` (`backend/requirements.txt`)  
**Companion library:** `langchain-core==0.3.28` is declared but **never imported** in application code. The graph talks to the LLM through the OpenAI SDK, not LangChain chat models.  
**Primary implementation file:** `backend/app/agents/workflow.py`

This document reverse-engineers the live LangGraph implementation. Every claim cites a file, function, and (where it matters) the actual code. It is written so an engineer who has never opened this repository can explain the architecture in an interview, debug a failed chat request, or extend the graph without guessing.

---

# Section 1 — Executive Summary

## Why is LangGraph used here?

This product answers questions about an indexed GitHub repository and **must cite file/line evidence**. A single LLM call over a dump of code would hallucinate paths. A free-form ReAct agent would call tools in an unpredictable order and be hard to test. LangGraph is used to freeze the answer pipeline into four named, sequential, inspectable stages:

1. **Classify** the question into retrieval *kinds* (`RAG`, `CODE`, `GRAPH`, `HISTORY`, `DOCS`).
2. **Retrieve** evidence from hybrid search, symbols, the dependency graph, git history, and markdown docs.
3. **Compose** a cited answer with a strict system prompt.
4. **Source-check** the answer; if it is not grounded, rewrite or refuse.

That is the entire reason LangGraph exists in this repo: **explicit retrieval stages + a grounding gate**, not tool-calling autonomy.

## What problem does it solve?

After a background worker clones a repo, parses it with Tree-sitter, builds a dependency graph, chunks source, and embeds into Postgres/pgvector, the user asks questions in a chat UI. The graph's job is:

- Choose *which* evidence stores to query.
- Fan those stores into a single context window.
- Force the LLM to answer only from that context.
- Reject answers that do not cite allowed sources.

Without the graph, chat would be "embed query → top-k chunks → one LLM call" with no classification, no git/graph/docs paths, and no citation verifier.

## What kind of workflow is implemented?

A **linear RAG orchestration pipeline** compiled as a `StateGraph`. It is **not** a looping agent. There are no conditional edges, no `Send` fan-out, no `ToolNode`, no human-in-the-loop `interrupt`, and no checkpointer.

| Question | Answer for this repo |
| -------- | -------------------- |
| Is it an agent? | **Narrowly yes, colloquially.** The package is `app/agents/` and the handbook calls it "the LangGraph agent." Architecturally it is a **deterministic 4-node pipeline**, not a ReAct/tool-calling agent. |
| Is it a multi-agent workflow? | **No.** One graph, one compiled app, one `invoke()`. Nodes are stages of the same worker, not collaborating agents. |
| Is it a RAG workflow? | **Yes. This is the primary pattern.** Hybrid vector + FTS + symbol + graph retrieval, with optional git-history and markdown-doc channels. |
| Is it an orchestrator? | **Yes.** `node_retrieve` orchestrates five retrieval backends. LangGraph itself only sequences classify → retrieve → answer → source_check. |
| Is it stateful? | **Conversation-stateful in Postgres** (`Conversation` / `Message`). **Not LangGraph-stateful.** Each `invoke()` starts from a fresh `AgentState`. There is no `MemorySaver` and no checkpoint. |
| Is it event-driven? | **No.** Chat is a synchronous FastAPI `POST`. Indexing is Redis-queue event-driven, but that pipeline never touches LangGraph. |

## One-paragraph architecture overview

A signed-in user sends `POST /api/repositories/{repo_id}/chat`. FastAPI rate-limits, checks repo ownership, optionally returns a Redis cache hit, then `workflow.chat()` loads or creates a Postgres conversation, formats the last six turns, and calls `run_agent()`. `run_agent()` `invoke()`s a process-wide compiled `StateGraph(AgentState)` whose entry point is `classify`. Classification is keyword-based (always includes `RAG`). Retrieval opens a **second** SQLAlchemy session, rewrites the query (LLM JSON or regex fallback), always runs multi-query hybrid RAG fused with Reciprocal Rank Fusion, and conditionally adds CODE / GRAPH / HISTORY / DOCS evidence. `compose_answer` dedupes chunks, builds a citation-strict prompt, and calls OpenRouter (`openai/gpt-4o-mini` by default) at `temperature=0.0`. `source_check` runs `is_grounded()`; on failure it retries with a stricter rewrite prompt, then replaces the answer with an ungrounded refusal. The assistant message and source JSONB are committed. The graph never streams tokens and never persists its internal state.

---

# Section 2 — Locate Every LangGraph File

A full-repository search for `langgraph`, `StateGraph`, `START`, `END`, `add_node`, `add_edge`, `add_conditional_edges`, `compile`, `Command`, `Send`, `ToolNode`, `MessagesState`, `interrupt`, `checkpoint`, and `MemorySaver` yields **one production import site**.

LangGraph APIs that **do not exist anywhere in this codebase:** `START`, `add_conditional_edges`, `Command`, `Send`, `ToolNode`, `MessagesState`, `interrupt`, `MemorySaver`, checkpointers, `stream()`, `astream()`, `batch()`.

`langchain-core` is listed in `backend/requirements.txt` because LangGraph 0.2 depends on it. Application code never imports it.

| File | Purpose | Important Symbols |
| ---- | ------- | ----------------- |
| `backend/app/agents/workflow.py` | **The only LangGraph graph.** Defines `AgentState`, four node functions, `build_graph()`, singleton `get_graph_app()`, `run_agent()`, `chat()`. | `StateGraph`, `END`, `add_node`, `add_edge`, `set_entry_point`, `compile`, `invoke` |
| `backend/app/agents/__init__.py` | Lazy re-export so `from app.agents import chat` does not import LangGraph until first use. | `__getattr__` → `chat`, `run_agent` |
| `backend/app/agents/query_utils.py` | Classification, query rewrite, citation regex, grounding, history formatting. Called **by** graph nodes; does not import LangGraph. | `classify_query`, `rewrite_query`, `is_grounded`, `ungrounded_refusal`, `format_history` |
| `backend/app/agents/tools.py` | Retrieval helpers invoked from `node_retrieve`. Not LangGraph `Tool` objects. | `search_code`, `search_symbol`, `find_references`, `find_dependencies`, `find_dependents`, `get_graph_path`, `get_git_history`, `tool_search_documentation` |
| `backend/app/rag/prompts.py` | System/user prompts for `compose_answer` and `source_check`. | `ANSWER_SYSTEM`, `SOURCE_CHECK_SYSTEM`, `build_answer_messages`, `build_source_check_messages` |
| `backend/app/rag/embeddings.py` | OpenAI-compatible `chat_completion` / `embed_texts`. Used by retrieve (rewrite + embeddings) and answer nodes. | `chat_completion`, `get_openai_client` |
| `backend/app/rag/retriever.py` | Hybrid RAG used by `search_code`. | `hybrid_retrieve`, `vector_search`, `keyword_search`, `symbol_search`, `search_documentation` |
| `backend/app/rag/fusion.py` | RRF merge of ranked lists. Used both inside hybrid retrieve and again in `node_retrieve` to merge multi-query RAG lists. | `reciprocal_rank_fusion` |
| `backend/app/rag/reranker.py` | Heuristic (default) or optional LLM rerank after fusion. | `rerank`, `heuristic_rerank`, `llm_rerank` |
| `backend/app/api/routes.py` | HTTP entry: `chat_repo` → `agent_chat`. | `chat_repo`, `agent_chat` |
| `backend/app/schemas.py` | Request/response contracts for chat. | `ChatRequest`, `ChatResponse`, `SourceRef` |
| `backend/app/models/__init__.py` | Persistence for conversations the graph does not checkpoint. | `Conversation`, `Message`, `Repository` |
| `backend/app/services/cache.py` | Redis cache of first-turn chat responses (outside the graph). | `chat_cache_key`, `cache_get`, `cache_set` |
| `backend/app/config.py` | Model, chunk limits, rerank mode. | `llm_model`, `context_chunks`, `retrieve_candidates`, `rerank_mode` |
| `backend/requirements.txt` | Pins `langgraph==0.2.60` and unused-by-app `langchain-core==0.3.28`. | — |
| `backend/tests/test_agent_v2.py` | Tests classify + citation helpers (not the compiled graph). | `classify_query`, `citations_match` |
| `backend/tests/test_rag_upgrade.py` | Tests rewrite, grounding, RRF, symbol hints. | `rewrite_query`, `is_grounded` |
| `backend/tests/eval_questions.json` | Offline eval contract for "must cite path X". | Flask-oriented gold questions |
| `frontend/src/api.ts` | Client `POST /api/repositories/{id}/chat`. | `api.chat` |
| `frontend/src/App.tsx` | `sendChat()` holds `conversationId` and messages. | `sendChat` |
| `PROJECT_ARCHITECTURE_HANDBOOK.md` | Existing high-level notes. This deep dive supersedes it for LangGraph. | — |

Files named `graph*` that are **not** LangGraph:

| File | What it actually is |
| ---- | ------------------- |
| `backend/app/services/graph_service.py` | Code **dependency graph** (symbols/edges in Postgres). |
| `frontend/src/GraphView.tsx` | React Flow visualization of that dependency graph. |
| `frontend/src/graphUtils.ts` | Client-side neighborhood layout. |

---

# Section 3 — Dependency Map

```
frontend/src/App.tsx  sendChat()
  → frontend/src/api.ts  api.chat()
    → POST /api/repositories/{repo_id}/chat
      → backend/app/main.py  FastAPI app
        → backend/app/middleware.py  RequestIdMiddleware
        → backend/app/api/routes.py  chat_repo()
            → auth.get_current_user()          JWT
            → repository_service.check_rate_limit()   Redis INCR
            → repository_service.get_repository()     ownership 404
            → cache.cache_get()                Redis, first turn only
            → workflow.chat()   [imported as agent_chat]
                → models.Conversation / Message     Postgres
                → query_utils.format_history()
                → workflow.run_agent()
                    → workflow.get_graph_app()
                        → workflow.build_graph()
                            StateGraph(AgentState)
                            nodes: classify, retrieve, compose_answer, source_check
                            compile()  → CompiledGraph
                    → CompiledGraph.invoke(initial_state)
                        → node_classify
                            → query_utils.classify_query()
                        → node_retrieve
                            → database.SessionLocal()   NEW session (not request db)
                            → query_utils.rewrite_query()
                                → embeddings.chat_completion()   (if LLM key set)
                            → query_utils.extract_symbol_hint()
                            → agents.tools.search_code()
                                → retriever.hybrid_retrieve()
                                    → embeddings.embed_texts()
                                    → retriever.vector_search()      pgvector <=>
                                    → retriever.keyword_search()     FTS search_tsv
                                    → retriever.symbol_search()      Symbol ILIKE
                                    → graph_service.expand_graph_neighbors()
                                    → fusion.reciprocal_rank_fusion()
                                    → reranker.rerank()
                            → fusion.reciprocal_rank_fusion()   (merge rewrite lists)
                            → agents.tools.search_symbol()      if CODE
                            → agents.tools.find_references()    if CODE
                            → agents.tools.find_dependencies()  if GRAPH
                            → agents.tools.find_dependents()    if GRAPH
                            → agents.tools.get_graph_path()     if GRAPH
                            → agents.tools.get_git_history()    if HISTORY
                                → git_history_service.get_git_history_context()
                            → agents.tools.tool_search_documentation()  if DOCS
                                → retriever.search_documentation()
                        → node_answer  (graph node name: "compose_answer")
                            → workflow._structured_notes()
                            → rag.prompts.build_answer_messages()
                            → embeddings.chat_completion()
                        → node_source_check
                            → query_utils.is_grounded()
                            → rag.prompts.build_source_check_messages()
                            → embeddings.chat_completion()   (retry path)
                            → query_utils.ungrounded_refusal()
                → db.add(Message assistant) + db.commit()
            → cache.cache_set()   if conversation_id was None
        ← ChatResponse { conversation_id, answer, sources }
```

### Why each connection exists

**Frontend → FastAPI.** Chat is a product feature, not a CLI. `App.tsx` keeps `conversationId` in React state so follow-ups reuse the same Postgres conversation. Chat is disabled until indexing `COMPLETED`.

**`chat_repo` → `workflow.chat`.** The route owns HTTP concerns (auth, 429, cache, Pydantic). The agent owns conversation persistence and graph execution. This split keeps LangGraph out of the router.

**`chat` → `run_agent`.** `chat()` is the persistence wrapper. `run_agent()` is the pure graph invocation. Tests *could* call `run_agent` without writing messages; today they do not.

**`run_agent` → compiled graph.** LangGraph's `invoke()` is the only execution API used. The compiled graph is a process singleton so `compile()` happens once per backend worker.

**Nodes → `query_utils`.** Classification, rewrite, and grounding are pure functions so they can be unit-tested without compiling a graph (`test_agent_v2.py`, `test_rag_upgrade.py`).

**`node_retrieve` → `agents.tools`.** Tools are a facade over services. The graph never imports `retriever.py` or `graph_service.py` directly. That is why unused helpers (`read_file`, `graph_summary`, …) look like "agent tools" even though LangGraph never binds them.

**`node_retrieve` → `SessionLocal()`.** The request session (`get_db`) is used for conversation CRUD. Retrieval opens a second session because the compiled graph has no place to inject the FastAPI `Session` (no `config={"configurable": ...}`). This is an architectural seam, not an accident.

**Answer / source_check → `rag.prompts` + `embeddings.chat_completion`.** Prompts are isolated from graph topology so citation policy can change without touching edges.

**Outside the graph: Redis cache.** First-turn identical questions skip the LLM. Follow-up turns (`conversation_id` set) never hit this cache, because history would make the same string mean something else.

**Outside the graph: indexing worker.** `worker/` and `indexing_pipeline.py` populate `code_chunks`, `symbols`, `graph_nodes`, `commits`. LangGraph only *reads* that data. If the repo is not indexed, retrieve returns empty chunks and `compose_answer` short-circuits.

---

# Section 4 — Graph Entry Point

There is **no CLI, scheduler, or worker** that runs the graph. Indexing workers never import `workflow.py`. Execution always starts from HTTP.

## 4.1 HTTP surface

```
POST /api/repositories/{repo_id}/chat
Authorization: Bearer <JWT>
Content-Type: application/json

{ "message": "<user question>", "conversation_id": "<optional uuid>" }
```

Defined in `backend/app/api/routes.py` as `chat_repo`. Schemas are `ChatRequest` / `ChatResponse` in `backend/app/schemas.py`.

FastAPI mounts the router in `backend/app/main.py`:

```python
app = FastAPI(title="GitHub Repository AI Assistant", version="5.0.0")
app.include_router(router)  # prefix="/api"
```

## 4.2 Preconditions before `invoke()`

`chat_repo` does **not** jump straight into LangGraph:

1. `get_settings()` — load env (model, rate limit, chunk caps).
2. `check_rate_limit(f"chat:{user.id}", settings.chat_rate_limit_per_minute)` — Redis `INCR` on `rl:chat:{user_id}:{minute_bucket}`; default 30/min; raises HTTP 429.
3. `get_current_user` — JWT Bearer required; 401 if missing.
4. `get_repository(db, repo_id, user_id=user.id)` — 404 if missing or not owned by the user.
5. If `body.conversation_id is None`: `cache_get(chat_cache_key(str(repo_id), body.message))`. Key is `chat:{repo_id}:{sha256(message)[:16]}`, TTL 300s. Cache **bypasses** the graph entirely.

Only then:

```python
conversation, answer, sources = agent_chat(
    db, repo_id, body.message, conversation_id=body.conversation_id, user_id=user.id
)
```

`agent_chat` is `from app.agents.workflow import chat as agent_chat`.

## 4.3 Actual call stack

```
Browser  App.tsx sendChat()
  → api.chat(repo.id, q, conversationId)
    → fetch POST /api/repositories/{id}/chat
      → RequestIdMiddleware.dispatch
      → chat_repo
        → check_rate_limit
        → get_repository
        → [optional cache_get → return]
        → workflow.chat
          → Conversation get/create + Message history load
          → Message(role=user) insert
          → workflow.run_agent
            → get_graph_app()          # compile once
            → compiled_graph.invoke(initial AgentState)
              → START (implicit via set_entry_point)
              → node "classify"        node_classify
              → node "retrieve"        node_retrieve
              → node "compose_answer"  node_answer
              → node "source_check"    node_source_check
              → END
          → Message(role=assistant, sources=JSONB) insert
          → db.commit()
        → ChatResponse
        → [optional cache_set]
```

## 4.4 What `invoke()` receives

`run_agent` always seeds **every** `AgentState` field. LangGraph 0.2 requires this for non-optional keys:

```python
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
```

`run_agent` returns only `(answer, sources)`. `citation_ok` is logged and discarded. The HTTP response never exposes the internal flag.

## 4.5 Frontend conversation wiring

`frontend/src/App.tsx` `sendChat()`:

- Guards: repo present, non-empty input, indexing completed, not already busy.
- Optimistic user bubble, then `api.chat(repo.id, q, conversationId)`.
- Stores `res.conversation_id` so the **next** request is a follow-up (skips Redis cache, loads history inside `chat()`).

Default input text is `"Where is the application factory defined?"` — the same question used in Section 16.

---

# Section 5 — Graph State Deep Dive

## 5.1 The TypedDict

```python
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
```

This is **not** `MessagesState`. The graph does not keep a LangChain message list. Chat history is a **pre-formatted string** injected once at invoke time.

## 5.2 Why `Annotated[list, operator.add]`?

LangGraph reducers define how node updates merge into state. For `chunks` and `notes`, a node return value is **concatenated**, not replaced.

| Field | Reducer | Practical effect in this graph |
| ----- | ------- | ------------------------------ |
| `chunks` | `operator.add` | `node_retrieve` returns a list that is appended to the initial `[]`. |
| `notes` | `operator.add` | Same for structured graph/reference notes. |
| All others | overwrite (default) | Latest node write wins. |

**Why it exists:** the handbook and comments talk about "fan-in." The intended LangGraph pattern is parallel retrieve nodes (`Send` to RAG/CODE/GRAPH workers) whose lists concatenate. **That pattern was never wired.** Fan-in happens *inside* `node_retrieve` with Python `list.extend`. The reducers are therefore vestigial but harmless on a linear graph: retrieve runs once, so `[] + retrieved = retrieved`.

**Trap:** if you later add a second node that also returns `chunks`, they will **append**, not replace. `compose_answer` and `source_check` must not return `chunks` unless you want duplication. They currently do not.

## 5.3 Field-by-field contract

### `repository_id: str`

| | |
| --- | --- |
| **Type** | UUID string, not `uuid.UUID`. |
| **Purpose** | Scope every DB lookup to one indexed repo. |
| **Written by** | `run_agent` initial state (`str(repository_id)`). Never updated by nodes. |
| **Read by** | `node_retrieve` (`uuid.UUID(state["repository_id"])`). |
| **Lifecycle** | Constant for the invoke. |

### `question: str`

| | |
| --- | --- |
| **Type** | Raw user message. |
| **Purpose** | Classification, rewrite, LLM user prompt, source-check user prompt. |
| **Written by** | `run_agent` from `chat()`'s `message`. |
| **Read by** | All four nodes (classify, retrieve, answer, source_check). |
| **Lifecycle** | Immutable during the run. Follow-ups are a **new** invoke with a new question string. |

### `history: str`

| | |
| --- | --- |
| **Type** | Plain text, `role: content` lines, last 6 messages, each truncated to 800 chars (`format_history`). |
| **Purpose** | Query rewrite (coreference: "who calls that?") and answer prompt ("Conversation so far"). |
| **Written by** | `run_agent` from `chat()` **before** the new user message is included in `prior` (see lifecycle note). |
| **Read by** | `node_retrieve` (`rewrite_query`, `extract_symbol_hint(history)`), `node_answer`. |
| **Lifecycle** | Built once. `source_check` does not receive history in its retry prompt. |

**Important:** `chat()` loads `prior` messages **before** inserting the current user row, then formats those as history. The current question is **not** duplicated into `history`. Correct.

### `kinds: list`

| | |
| --- | --- |
| **Type** | `list[str]`, values from `{RAG, HISTORY, DOCS, GRAPH, CODE}`. |
| **Purpose** | Tells retrieve which extra channels to run. `RAG` is always present. |
| **Written by** | Initial `[]`, then `node_classify` overwrites with `classify_query(...)`. |
| **Read by** | `node_retrieve` (`set(state.get("kinds") or ["RAG"])`). |
| **Lifecycle** | Set once at classify. Not a router key — the graph **always** goes classify → retrieve. |

### `chunks: Annotated[list, operator.add]`

| | |
| --- | --- |
| **Type** | List of dicts with at least `file`, `start_line`, `end_line`, `content`, often `score`, `method_name`, `language`. |
| **Purpose** | Evidence window for the LLM and for citation checking. |
| **Written by** | `node_retrieve` (RAG lists, symbol hits, git history dicts, markdown hits). |
| **Read by** | `node_answer` (dedupe + context), `node_source_check` (grounding). |
| **Lifecycle** | Empty → retrieve fills → remaining nodes read. Git chunks use synthetic files like `_git_churn` or `commit:abc1234`. |

### `notes: Annotated[list, operator.add]`

| | |
| --- | --- |
| **Type** | List of strings, prefixed `references:`, `dependencies:`, `dependents:`, `neighbors:`. |
| **Purpose** | Compact graph facts. Not shown as user-facing sources. Injected as a fake chunk `_agent_notes` for the LLM only. |
| **Written by** | `node_retrieve` when CODE or GRAPH kinds fire. |
| **Read by** | `node_answer` via `_structured_notes`. Filtered out of `sources` and of grounding chunk lists. |
| **Lifecycle** | Empty unless CODE/GRAPH retrieval runs. |

### `answer: str`

| | |
| --- | --- |
| **Type** | Markdown/plain LLM text, or a hardcoded refusal. |
| **Purpose** | What the user sees. |
| **Written by** | `node_answer` (LLM or fallback snippet / empty-context message); possibly overwritten by `node_source_check` (revised answer or `ungrounded_refusal`). |
| **Read by** | `node_source_check`; then `run_agent` / `chat()`. |
| **Lifecycle** | `""` → compose → maybe rewrite/refuse. |

### `sources: list`

| | |
| --- | --- |
| **Type** | `{file, start_line, end_line, snippet[:400]}` for real files only (`_agent_notes` excluded). |
| **Purpose** | UI "Key Files" / citation chips; stored on `Message.sources` JSONB. |
| **Written by** | `node_answer` only. `source_check` does **not** recompute sources after a rewrite. |
| **Read by** | `node_source_check` (merged into grounding chunk list); HTTP response. |
| **Lifecycle** | Empty → compose fills. A source-check rewrite can change the **answer text** while leaving **sources** pointing at the original retrieve set. That is intentional: sources are retrieved evidence, not "what the model cited." |

### `citation_ok: bool`

| | |
| --- | --- |
| **Type** | bool |
| **Purpose** | Internal quality flag. Logged. **Not** returned to the client. |
| **Written by** | Initial `False`; `node_answer` sets `False` on empty context; `node_source_check` sets True/False. |
| **Read by** | `run_agent` logger only. |
| **Lifecycle** | Diagnostic. A True value still can be an honest "not found in indexed sources" (`is_grounded` treats insufficiency language as success). |

## 5.4 State change across execution (shape)

**Before `invoke` (seed):**

```python
{
  "repository_id": "a1b2-...",
  "question": "Where is the application factory defined?",
  "history": "",            # or "user: ...\nassistant: ..."
  "kinds": [],
  "chunks": [],
  "notes": [],
  "answer": "",
  "sources": [],
  "citation_ok": False,
}
```

**After `classify`:** `kinds` becomes `["RAG", "CODE"]` (keyword `defined`). Everything else unchanged.

**After `retrieve`:** `chunks` is a list of code/doc/git dicts (reducer: `[] + retrieved`). `notes` may contain `references:create_app:[...]`.

**After `compose_answer`:** `answer` is LLM text with `path:start-end` citations; `sources` is the truncated evidence list. `citation_ok` still False unless empty-context path set it False explicitly (empty path also sets answer + empty sources).

**After `source_check`:** `citation_ok` True if grounded (or insufficiency phrase); else answer replaced with refusal and `citation_ok` False.

---

# Section 6 — Complete Node Breakdown

Graph node names (wires) vs Python functions (implementation):

| Graph name | Function | File |
| ---------- | -------- | ---- |
| `classify` | `node_classify` | `backend/app/agents/workflow.py` |
| `retrieve` | `node_retrieve` | `backend/app/agents/workflow.py` |
| `compose_answer` | `node_answer` | `backend/app/agents/workflow.py` |
| `source_check` | `node_source_check` | `backend/app/agents/workflow.py` |

---

## 6.1 Node `classify` — `node_classify`

**Signature:** `def node_classify(state: AgentState) -> dict`

**Responsibility:** Tag the question with one or more retrieval channels. Always includes `RAG`. Does not call the LLM. Does not touch the database.

**Input state:** `state["question"]`

**Output state:** `{"kinds": list[str]}`

**Side effects:** None.

**External calls:** `classify_query(question)` in `backend/app/agents/query_utils.py`.

### Code

```python
def node_classify(state: AgentState) -> dict:
    return {"kinds": classify_query(state["question"])}
```

Line-by-line:

1. Read the user question from graph state.
2. Delegate to a pure function so classification is unit-testable without LangGraph.
3. Return a partial state update; LangGraph overwrites `kinds`.

### `classify_query` logic (this is the real work)

```python
def classify_query(question: str) -> list[str]:
    """Return one or more retrieval paths. Always includes RAG."""
    q = question.lower()
    kinds = ["RAG"]
    # HISTORY if churn/commit/diff/history/...
    # DOCS if readme/documentation/how do i/install/...
    # GRAPH if depend/call/import/who uses/graph/path
    # CODE if where is/defined/definition/symbol/function/class/method
    # then stable-dedupe preserving order
```

**Why a node at all?** It could be inlined into retrieve. Keeping it as a node makes traces (`classify` then `retrieve`) match the product story: "we classify, then we retrieve." Interview answer: observability and test seams, not routing — because there are **no conditional edges**.

**Limitation:** Keyword bags miss intent. "Explain how auth works" is RAG-only; "Where is auth defined?" adds CODE. "Who calls X?" adds GRAPH (`call`). False positives: "between" triggers HISTORY even for "difference between A and B" in code.

---

## 6.2 Node `retrieve` — `node_retrieve`

**Signature:** `def node_retrieve(state: AgentState) -> dict`

**Responsibility:** Build the evidence list. This is the heaviest node: query rewrite, up to three hybrid searches, optional symbol/graph/git/docs, RRF merge, logging.

**Input state:** `repository_id`, `question`, `history`, `kinds`

**Output state:** `{"chunks": chunks, "notes": notes}`

**Side effects:** Opens and **closes** a SQLAlchemy session; embedding API calls; optional rewrite LLM call; INFO log with latency.

**External calls:** listed in Section 3.

### Session handling

```python
def _db() -> Session:
    from app.database import SessionLocal
    return SessionLocal()

def node_retrieve(state: AgentState) -> dict:
    db = _db()
    ...
    try:
        ...
        return {"chunks": chunks, "notes": notes}
    finally:
        db.close()
```

**Why a local import of `SessionLocal`?** Avoid circular imports at module load (`workflow` → `database` → models → config). **Why not the request `db`?** `run_agent(db, ...)` receives the FastAPI session but **never passes it into `invoke()`**. Retrieve cannot see it. Consequence: conversation writes and retrieval **cannot share a transaction**. Retrieve always sees committed index data, which is correct for this product (index is a different worker).

### Body, line-by-line intent

```python
repo_id = uuid.UUID(state["repository_id"])
q = state["question"]
history = state.get("history") or ""
kinds = set(state.get("kinds") or ["RAG"])
```

- Parse UUID. Invalid id would throw and fail the whole invoke (no try around this).
- Default kinds to `{RAG}` if classify somehow returned empty.

```python
rewritten = rewrite_query(q, history)
rewrites = rewritten.get("rewrites") or [q]
symbols = rewritten.get("symbols") or []
hint = symbols[0] if symbols else extract_symbol_hint(q)
if not hint and history:
    hint = extract_symbol_hint(history)
```

- Multi-query: 2–3 search strings + identifier list.
- Follow-up support: "who calls that?" may have no identifier in the new question; history is scanned for CamelCase / snake_case / backticks.

```python
for rq in rewrites[:3]:
    rag_lists.append(agent_tools.search_code(db, repo_id, rq))
if rag_lists:
    chunks.extend(reciprocal_rank_fusion(rag_lists, limit=settings.context_chunks))
```

- **RAG always runs**, even if kinds is only HISTORY. Comment on the function: `"Fan-in: always RAG (multi-query); also CODE/GRAPH/HISTORY/DOCS when classified."`
- Each rewrite triggers a full `hybrid_retrieve` (embed + FTS + symbol + 1-hop graph + RRF + rerank). Cap 3 rewrites to bound embed cost.
- Second RRF merges the three ranked lists down to `context_chunks` (default 10).

**CODE branch:**

```python
if "CODE" in kinds:
    for sym in (symbols[:3] if symbols else ([hint] if hint else [])):
        chunks.extend(agent_tools.search_symbol(db, repo_id, sym, limit=8))
        refs = agent_tools.find_references(db, repo_id, sym)
        notes.append(f"references:{sym}:{refs[:20]}")
```

Extra symbol-table hits plus incoming dependency edges as notes (not as chunks).

**GRAPH branch:**

```python
if "GRAPH" in kinds:
    name = hint or (symbols[0] if symbols else None)
    if name:
        deps = agent_tools.find_dependencies(...)
        refs = agent_tools.find_dependents(...)
        neighbors = agent_tools.get_graph_path(..., hops=2)
        notes.append(...)
        chunks.extend(agent_tools.search_symbol(..., limit=6))
```

If classification says GRAPH but no identifier could be extracted, **the GRAPH branch is a no-op**.

**HISTORY branch:**

```python
if "HISTORY" in kinds:
    repo = db.get(Repository, repo_id)
    hist = agent_tools.get_git_history(db, repo_id, q, local_path=repo.local_path if repo else None)
    chunks.extend(hist)
```

Uses clone path when present so `compare_commits` can `git diff` two SHAs.

**DOCS branch:**

```python
if "DOCS" in kinds:
    for rq in rewrites[:2]:
        chunks.extend(agent_tools.tool_search_documentation(db, repo_id, rq, limit=6))
```

Markdown-only vector + FTS + RRF + rerank. Two rewrites max.

Logging:

```python
logger.info("retrieve repo=%s kinds=%s rewrites=%s chunks=%s ms=%s", ...)
```

No structured trace id is passed into this log (request id lives in middleware logs only).

---

## 6.3 Node `compose_answer` — `node_answer`

**Signature:** `def node_answer(state: AgentState) -> dict`

**Graph name is `compose_answer`** so traces read as a pipeline; the Python name is the shorter `node_answer`.

**Responsibility:** Deduplicate evidence, optionally inject graph notes, call the LLM, build UI sources. If there is no usable context, return a static refusal **without** calling the LLM.

**Input state:** `chunks`, `notes`, `question`, `history`

**Output state:** `{answer, sources}` or `{answer, sources, citation_ok: False}` on empty context.

**Side effects:** OpenRouter chat completion; exception fallback that **inlines the top chunk** into the answer (and mentions the exception). Timing log `llm_answer ms=`.

### Dedup

```python
dedup: dict[str, dict] = {}
for c in state.get("chunks") or []:
    if "file" not in c:
        continue
    key = f"{c['file']}:{c.get('start_line')}:{c.get('end_line')}"
    dedup.setdefault(key, c)
context_chunks = list(dedup.values())[: settings.context_chunks]
```

- Drops malformed dicts without `file`.
- First-seen wins (`setdefault`), so RAG order (already RRF-sorted) beats later CODE/GRAPH appends for the same span.
- Hard cap `context_chunks` (default 10) **after** retrieve may have already appended CODE/HISTORY extras. Retrieve's RRF cap is 10, but CODE/HISTORY/DOCS `extend` can grow the list well beyond 10 before this slice.

### Notes → fake chunk

```python
structured = _structured_notes(state.get("notes") or [])
if structured:
    context_chunks.append({
        "file": "_agent_notes",
        "start_line": 1,
        "end_line": 1,
        "content": structured,
    })
```

`_structured_notes` keeps only strings starting with `dependencies:`, `dependents:`, `references:`, `neighbors:`. Raw dumps would be dropped (defensive).

`_agent_notes` is later:

- included in the LLM context,
- **excluded** from `sources` sent to the UI,
- **excluded** from grounding file lists (so the model cannot "cite" `_agent_notes:1-1` as success unless `is_grounded` finds a real path — and `_agent_notes` is not a real path in retrieve chunks used for match... actually source_check filters `_agent_notes` out of chunks. Good.).

### Empty context short-circuit

```python
if not context_chunks:
    return {
        "answer": (
            "I could not find relevant code context for that question. "
            "Index the repository first or rephrase."
        ),
        "sources": [],
        "citation_ok": False,
    }
```

This message contains "could not find", so `is_grounded` would treat it as success — but `citation_ok` is already False. `source_check` still runs (linear edge). `is_grounded` on this text returns **True** (insufficiency phrase). Then `source_check` keeps the answer and sets `citation_ok: True`. So the empty-context `citation_ok: False` from `node_answer` is **overwritten to True** by source_check. The user still sees the helpful "index first" message. The log will say `citation_ok=true`. Subtle.

### LLM call

```python
messages = build_answer_messages(state["question"], context_chunks, history=history)
try:
    answer = chat_completion(messages, temperature=0.0)
except Exception as exc:
    logger.exception("LLM call failed")
    top = context_chunks[0]
    answer = (
        f"Relevant code appears in {top['file']}:{top['start_line']}-{top['end_line']}.\n\n"
        f"```\n{top['content'][:1200]}\n```\n\n(LLM unavailable: {exc})"
    )
```

Fallback **is already cited** (`file:start-end`), so source_check will usually pass even when the LLM is down — as long as that file is in `chunks`.

### Sources payload

```python
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
```

Does **not** filter `_git_churn` / `commit:...`. The UI may show those as "files." Citation click-through (`onOpenCitation`) expects a real repo path; synthetic names will fail to open in the source drawer. Product gap.

---

## 6.4 Node `source_check` — `node_source_check`

**Signature:** `def node_source_check(state: AgentState) -> dict`

**Responsibility:** Grounding gate. Either accept the draft, rewrite it with a stricter prompt, or replace it with a refusal that lists candidate files **as unconfirmed**.

**Input state:** `answer`, `chunks`, `sources`, `question`

**Output state:** `{"citation_ok": True, "answer": answer}` or `{"answer": answer, "citation_ok": ok}`

**Side effects:** Optional second LLM call; logs.

### Chunk assembly for grounding

```python
chunks = [
    c for c in (state.get("chunks") or [])
    if c.get("file") and c.get("file") not in {"_agent_notes"}
]
for s in state.get("sources") or []:
    chunks.append({
        "file": s.get("file"),
        "start_line": s.get("start_line", 1),
        "end_line": s.get("end_line", 1),
        "content": s.get("snippet") or "",
    })
```

Sources are a subset of chunks (minus notes, plus 400-char snippets). Appending them duplicates file names; `citations_match` only cares that cited paths are in the allowed set.

### Git exception

```python
ok = is_grounded(answer, chunks)
if not ok and any(
    str(c.get("file", "")).startswith("commit:") or str(c.get("file", "")).startswith("_git")
    for c in chunks
):
    if re.search(r"commit:[0-9a-fA-F]{7,40}", answer) or CITATION_RE.search(answer):
        ok = True
```

History answers are allowed to cite `commit:sha` even when path matching is messy, **if** the retrieve set actually contained git chunks.

### Retry then refuse

```python
if ok:
    return {"citation_ok": True, "answer": answer}

messages = build_source_check_messages(state["question"], answer, chunks[:12])
revised = chat_completion(messages, temperature=0.0)
...
ok = is_grounded(answer, chunks)

if not ok:
    answer = ungrounded_refusal(chunks)
    ok = False
```

Retry failures are swallowed (`logger.warning`); then refusal.

`ungrounded_refusal` produces text containing `"candidates (not confirmed"`, which `is_grounded` treats as **True** (explicit insufficiency). `source_check` still returns `citation_ok: False` because it sets `ok = False` **after** generating the refusal, without re-running `is_grounded`. Consistent: refusal is a failed grounding, even though the same text would pass `is_grounded` if fed back in.

---

# Section 7 — Graph Flow Visualization

## 7.1 As compiled (this is the real topology)

```
                    set_entry_point
                           |
                           v
                      [ classify ]
                           |
                           |  unconditional add_edge
                           v
                      [ retrieve ]
                           |
                           |  unconditional add_edge
                           v
                   [ compose_answer ]
                           |
                           |  unconditional add_edge
                           v
                    [ source_check ]
                           |
                           |  add_edge(..., END)
                           v
                          END
```

There is **no branch**. Classification does not choose a subgraph. Extra retrieval is `if "CODE" in kinds` inside one node.

## 7.2 What happens *inside* `retrieve` (logical fan-in)

```
                    rewrite_query(question, history)
                           |
          +----------------+------------------+
          |                |                  |
          v                v                  v
     rewrite[0]       rewrite[1]         rewrite[2]
          |                |                  |
          v                v                  v
    hybrid_retrieve  hybrid_retrieve   hybrid_retrieve
          |                |                  |
          +--------+-------+------------------+
                   |
                   v
          reciprocal_rank_fusion  →  base RAG chunks
                   |
     +-------------+-------------+-------------+
     |             |             |             |
     v             v             v             v
  if CODE      if GRAPH     if HISTORY     if DOCS
  symbol+refs  deps+path    git context    markdown
     |             |             |             |
     +------+------+------+------+-------------+
            |
            v
     chunks + notes  →  return
```

## 7.3 What happens *inside* `source_check`

```
  is_grounded(answer, chunks)?
       |
       +-- yes --> keep answer, citation_ok=true --> END
       |
       +-- no, but git chunks + commit/path citation --> treat ok --> END
       |
       +-- no --> LLM rewrite (stricter prompt)
              |
              +-- now grounded --> END
              |
              +-- still no --> ungrounded_refusal --> END
```

## 7.4 Why this shape

A conditional graph (`CODE` → symbol node, `HISTORY` → git node, else RAG) would skip work. The authors preferred **always-on RAG plus additive channels** so a "where is X defined?" question still gets semantic neighbors, not only the symbol table. Cost: every chat pays at least one embed + FTS + symbol query, even for "highest churn" (which also runs HISTORY).

---

# Section 8 — Edges

## 8.1 Normal edges (all of them)

From `build_graph()`:

```python
graph.set_entry_point("classify")
graph.add_edge("classify", "retrieve")
graph.add_edge("retrieve", "compose_answer")
graph.add_edge("compose_answer", "source_check")
graph.add_edge("source_check", END)
```

| Edge | Type | Why it exists |
| ---- | ---- | ------------- |
| implicit START → `classify` | entry | Questions must be tagged before retrieve reads `kinds`. |
| `classify` → `retrieve` | unconditional | Retrieve always needs kinds (defaults to RAG if missing). |
| `retrieve` → `compose_answer` | unconditional | Answer never skips retrieve; empty retrieve is handled inside `node_answer`. |
| `compose_answer` → `source_check` | unconditional | Grounding is mandatory, including for fallbacks and empty-context messages. |
| `source_check` → `END` | terminal | Single-pass; no repair loop back to retrieve. |

## 8.2 Conditional edges

**None.** `add_conditional_edges` is unused.

Interview trap: "How does routing work?" The honest answer is: **it doesn't, at the graph layer.** Routing is `if "GRAPH" in kinds` inside `node_retrieve`.

## 8.3 Looping edges

**None.** Failed grounding does **not** go back to retrieve with a better query. It only rewrites the answer or refuses. That is a deliberate product choice: do not keep spending retrieval budget when the model cannot cite what it already has.

If you wanted a loop, you would add something like:

```python
graph.add_conditional_edges(
    "source_check",
    lambda s: "retrieve" if not s["citation_ok"] and s.get("retries", 0) < 1 else END,
)
```

That does not exist today. `retries` is not a state field.

## 8.4 The condition that *looks* like routing

```python
kinds = set(state.get("kinds") or ["RAG"])
...
if "CODE" in kinds:
    ...
if "GRAPH" in kinds:
    ...
if "HISTORY" in kinds:
    ...
if "DOCS" in kinds:
    ...
```

This is ordinary Python inside one node, not LangGraph conditional edges. Multiple kinds can be true at once (`"How do I install this? Check the README"` → `RAG` + `DOCS`).

---

# Section 9 — LangGraph APIs Used

## 9.1 `StateGraph`

- **Purpose:** Build a directed graph whose nodes are callables `(state) -> partial_state`.
- **Syntax:** `graph = StateGraph(AgentState)`
- **Where:** `build_graph()` in `workflow.py`.
- **Why chosen:** `AgentState` is a custom TypedDict (repo id, kinds, chunks). `MessagesState` would fight the design — history is a string, tools are not bound tools.

## 9.2 `END`

- **Purpose:** Terminal sentinel for `add_edge`.
- **Syntax:** `graph.add_edge("source_check", END)`
- **Where:** `build_graph()`.
- **Why:** Four-stage pipeline must terminate. No looping supervisor.

## 9.3 `START`

- **Not used.** Equivalent: `graph.set_entry_point("classify")`, which wires the implicit start node to `classify`.

## 9.4 `add_node`

- **Purpose:** Register named callables.
- **Syntax:** `graph.add_node("classify", node_classify)`
- **Where:** four calls in `build_graph()`.
- **Why:** Names appear in logs/traces; `compose_answer` is a product-facing name wrapping `node_answer`.

## 9.5 `add_edge`

- **Purpose:** Unconditional transition.
- **Where:** four edges listed in Section 8.
- **Why:** The control flow is a pipeline, not a router.

## 9.6 `add_conditional_edges`

- **Not used.** See Section 8.2.

## 9.7 `compile`

- **Purpose:** Freeze topology into a `CompiledGraph` / Pregel runtime.
- **Syntax:** `return graph.compile()`
- **Where:** end of `build_graph()`.
- **Why chosen:** Default compile — **no checkpointer, no interrupt_before, no debug**. `compile()` with no args means in-memory, ephemeral execution.

## 9.8 `invoke`

- **Purpose:** Run the graph to completion; return final state dict.
- **Syntax:** `result = app.invoke({...full AgentState...})`
- **Where:** `run_agent()`.
- **Why:** FastAPI handler is synchronous. The team did not implement SSE/token streaming, so blocking `invoke` matches the HTTP model.

## 9.9 `stream` / `astream` / `batch`

- **Not used.**

## 9.10 `ToolNode`

- **Not used.** Tools are plain functions in `agents/tools.py`.

## 9.11 `Command` / `Send`

- **Not used.** No dynamic fan-out to parallel retrieve nodes.

## 9.12 `interrupt`

- **Not used.** No human approval step.

## 9.13 Checkpoint / `MemorySaver`

- **Not used.** Conversation memory is Postgres `messages`, not LangGraph checkpoints.

## 9.14 `MessagesState`

- **Not used.** Custom `AgentState` instead.

## 9.15 Reducers (`Annotated[..., operator.add]`)

- **Purpose:** List concatenation on `chunks` and `notes`.
- **Where:** `AgentState` fields.
- **Why chosen:** Prepared for parallel retrieve nodes. Currently redundant with in-node `extend`.

## 9.16 Singleton compiled app

```python
_GRAPH = None

def get_graph_app():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH
```

- **Why:** `compile()` is not free; nodes close over no request-specific data, so one app per process is correct.
- **Threading:** Uvicorn workers each have their own `_GRAPH`. Fine.
- **Testing:** Mutating `_GRAPH` in tests would leak; tests currently avoid compiling the graph.

---

# Section 10 — Tool Integration

## 10.1 This is not LangGraph tool calling

There is no `bind_tools`, no OpenAI tool schema, no `ToolNode`, no agent loop that decides `tool_calls`. The LLM in `compose_answer` **cannot** request `read_file`. All retrieval is decided by `classify_query` + `node_retrieve`.

Calling these "tools" is still accurate as a **facade name** (`agents/tools.py`).

## 10.2 How they are "registered"

They are **not registered**. `node_retrieve` imports `from app.agents import tools as agent_tools` and calls functions directly.

## 10.3 Tools that **are** on the retrieve path

| Function | File | What it does | When called |
| -------- | ---- | ------------ | ----------- |
| `search_code` | `tools.py` | `hybrid_retrieve(...)` | Always, once per rewrite (max 3) |
| `search_symbol` | `tools.py` | `symbol_search` | CODE (up to 3 symbols, limit 8); GRAPH (limit 6) |
| `find_references` | `tools.py` | `Dependency.target_name ILIKE` | CODE |
| `find_dependencies` | `tools.py` | `Dependency.source_name ILIKE` | GRAPH |
| `find_dependents` | `tools.py` | alias of `find_references` | GRAPH |
| `get_graph_path` | `tools.py` | `expand_graph_neighbors(..., hops=2)` | GRAPH |
| `get_git_history` | `tools.py` | `get_git_history_context` | HISTORY |
| `tool_search_documentation` | `tools.py` | `search_documentation` | DOCS |

## 10.4 Tools that exist but are **dead** (never called)

Confirmed by repo-wide Python search: only defined in `tools.py`.

| Function | Intended job |
| -------- | ------------ |
| `read_file` | Load `FileRecord.content` (fuzzy path), cap 8000 chars |
| `get_file_structure` | List file paths |
| `tool_knowledge_tree` | `get_knowledge_tree` |
| `graph_summary` | Sample graph nodes/edges |

These look like leftovers from a planned ReAct agent. They are safe to delete or to wire later; they do not affect production chat.

## 10.5 Tool execution lifecycle (live path)

```
node_retrieve
  → agent_tools.search_code(db, repo_id, rewrite)
    → hybrid_retrieve
      → embed_texts([query])          # OpenRouter embeddings
      → vector_search SQL
      → keyword_search SQL (FTS or ILIKE fallback)
      → symbol_search ORM
      → expand_graph_neighbors BFS
      → reciprocal_rank_fusion
      → rerank (heuristic or llm)
  → return list[dict]
```

Errors:

- `vector_search` / `search_documentation`: embed failure → empty list, warning log, continue.
- `keyword_search`: FTS exception → ILIKE fallback.
- `rewrite_query`: LLM/JSON failure → regex fallback.
- Tool functions themselves have **no try/except**. A DB outage in retrieve fails the node and the HTTP request (middleware logs `request_error`).

## 10.6 Hybrid retrieve internals (what `search_code` actually runs)

`hybrid_retrieve` in `backend/app/rag/retriever.py`:

1. `vector_search` — cosine distance via pgvector `<=>`, score `1/(1+distance)`.
2. `keyword_search` — `plainto_tsquery('simple', ...)` so identifiers are not stemmed away.
3. `symbol_search` — token ILIKE on `symbols.name`, then overlapping `CodeChunk` or file snippet.
4. Graph expansion — 1 hop from symbol hit names; snippets from neighbor symbols.
5. RRF with `k=60`, cap `retrieve_candidates` (default 40).
6. `rerank(..., limit=context_chunks)` (default 10).

So each chat with 3 rewrites can mean **3 embedding API calls** plus 3× (vector + FTS + symbol + graph) SQL, **before** CODE/DOCS extras.

---

# Section 11 — LLM Integration

## 11.1 Provider and models

`backend/app/config.py` / `backend/app/rag/embeddings.py`:

| Setting | Default | Role |
| ------- | ------- | ---- |
| `llm_base_url` | `https://openrouter.ai/api/v1` | OpenAI-compatible gateway |
| `llm_api_key` | `""` | Required for rewrite + chat; empty → rewrite fallback, chat still tries (client key `"missing"`) |
| `llm_model` | `openai/gpt-4o-mini` | Chat completions |
| `embedding_model` | `openai/text-embedding-3-small` | Query + index embeddings |
| `embedding_dimensions` | `1536` | pgvector column size |

Client:

```python
def get_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.llm_api_key or "missing", base_url=settings.llm_base_url)

def chat_completion(messages: list[dict], temperature: float = 0.2) -> str:
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""
```

Not LangChain. No retries, no timeout override, no `max_tokens`, no streaming (`stream=True` absent).

## 11.2 Where the LLM is called (complete list)

| Call site | Temperature | Structured? | On failure |
| --------- | ----------- | ----------- | ---------- |
| `query_utils.rewrite_query` | `0.0` | JSON `{rewrites, symbols}` parsed with `json.loads` | Regex `_fallback_rewrite` |
| `reranker.llm_rerank` | `0.0` | JSON array `{i, score}` | Heuristic rerank. **Only if `rerank_mode=llm`** (default is `heuristic`, so this usually never runs) |
| `node_answer` | `0.0` | Free text with mandatory citations | Inline top chunk + exception string |
| `node_source_check` retry | `0.0` | Free text "corrected answer only" | `ok = False`, then refusal |

All graph-facing chat calls override the function default `0.2` with **`temperature=0.0`** for determinism (citations, JSON).

## 11.3 Trace of one user question through LLM

Example: `"Where is the application factory defined?"`

1. **Rewrite** (if `llm_api_key` set): system "You rewrite software repository questions for code search. Return JSON..." → e.g. `{"rewrites":["application factory","create_app factory"], "symbols":["create_app"]}`. Original question is prepended if missing → still max 3.
2. **Embeddings:** `embed_texts([rewrite])` per rewrite inside `vector_search` / docs search.
3. **Answer:** `ANSWER_SYSTEM` + context blocks + question.
4. **Optional source_check:** `SOURCE_CHECK_SYSTEM` + draft + allowed source list.

No prompt caching, no LangSmith, no token usage logged.

## 11.4 Streaming

**Off.** `chat_completion` waits for `choices[0].message.content`. The UI shows a busy spinner (`chatBusy`), not tokens.

---

# Section 12 — Prompt Flow

All graph prompts live in two places: `backend/app/rag/prompts.py` (answer + source check) and inline messages in `query_utils.rewrite_query` / `reranker.llm_rerank`.

## 12.1 System prompt — answer (`ANSWER_SYSTEM`)

Used by: **`node_answer`** via `build_answer_messages`.

```
You are a repository AI assistant. Answer questions using ONLY the provided source context.
Do not invent file paths, APIs, function names, or behavior that is not in the snippets.
Always cite sources using the format path:start-end (example: src/auth/service.py:10-42).
For git-history context you may also cite commit:sha (example: commit:abc1234).
If the context is insufficient, clearly say what is missing and that it was not found in the indexed sources.
Be precise and technical. Every factual claim must be backed by a citation.
```

**Why it exists:** The product's differentiator is citations. Grounding in `source_check` is regex-based; this prompt is what makes the first draft likely to pass.

## 12.2 User prompt — answer

Built by `build_answer_messages`:

```
[optional] Conversation so far:
{history}

Question: {question}

Source context:
[1] {file}:{start}-{end}
```
{content}
```

Provide an answer with citations, using only the source context.
```

`build_context_block` numbers chunks `[1]`, `[2]`, … so the model can refer to them; citations still must be `path:start-end`, not `[1]`. `is_grounded` does **not** accept `[1]` as a citation.

## 12.3 System prompt — source check (`SOURCE_CHECK_SYSTEM`)

Used by: **`node_source_check`** on the retry path only.

```
Rewrite the answer so every factual claim is entailed by the provided snippets
and backed by a citation in the form path:start-end or commit:sha from the allowed sources list.
Strip or remove any claim that is not supported by the snippets.
Do not invent file paths or APIs. If nothing is grounded, say it was not found in the indexed sources.
Keep the answer concise. Return the corrected answer only.
```

User side includes Question, Draft answer, Allowed sources (file:line list **without** snippet bodies in the "Allowed sources" section — snippets are **not** re-attached here). The retry model sees allowed **paths** more than code. That can make rewrite weaker than the original compose call (which had full snippet bodies). The compose prompt had the code; source_check retry may only have paths in "Allowed sources" plus the draft. **This is a real quality gap.**

Wait, let me re-read `build_source_check_messages`:

```python
sources = "\n".join(
    f"- {c['file']}:{c.get('start_line', 1)}-{c.get('end_line', 1)}" for c in chunks
)
...
f"Question: {question}\n\nDraft answer:\n{answer}\n\n"
f"Allowed sources:\n{sources}\n\nReturn the corrected answer only."
```

Confirmed: **no snippet bodies on retry.** The model must correct citations using the draft + path list only. It cannot re-read code unless the draft already quoted it.

## 12.4 Rewrite prompt (not in `prompts.py`)

System:

```
You rewrite software repository questions for code search.
Return JSON with keys rewrites (array of 2-3 short search queries)
and symbols (array of likely code identifiers like function/class names).
No markdown.
```

User: `Question: {question}` plus optional `Recent conversation:\n{history}`.

Node: **`retrieve`** (via `rewrite_query`).

## 12.5 Rerank prompt (optional)

Only if `RERANK_MODE=llm`. System asks for JSON `{i, score}`. Used inside `hybrid_retrieve`, not as a graph node.

## 12.6 Tool prompts

**None.** No tool-calling schemas.

## 12.7 Prompt chaining

Yes, but **linear and optional**:

```
rewrite (JSON) → [retrieval, no LLM] → ANSWER_SYSTEM compose → [if ungrounded] SOURCE_CHECK_SYSTEM rewrite
```

History is injected into rewrite and compose, **not** into source_check. A follow-up that is ungrounded is rewritten without conversation context.

---

# Section 13 — Memory

## 13.1 What this project does **not** use

| Mechanism | Status |
| --------- | ------ |
| LangGraph `MemorySaver` | Not used |
| Sqlite/Postgres/Redis checkpointer | Not used |
| Thread IDs / `configurable.thread_id` | Not used |
| `interrupt` + resume | Not used |

Each `invoke()` is stateless. You cannot `get_state()` a previous run.

## 13.2 Conversation memory (Postgres)

`workflow.chat()`:

1. Resolve `Conversation` by `conversation_id`, matching `repository_id` and optional `user_id`.
2. If missing/mismatched, create a new `Conversation(repository_id, user_id)`.
3. Load all `Message` rows ordered by `created_at`.
4. `format_history(..., max_turns=6)` — last 6 messages, each content sliced to 800 chars.
5. Insert user message.
6. `run_agent(..., history=history)`.
7. Insert assistant message with `sources` JSONB.
8. `commit` + `refresh`.

Models: `Conversation` and `Message` in `backend/app/models/__init__.py`.

This is **application memory**, not graph memory. If the process crashes after `invoke` but before `commit`, the graph result is lost (user message may already be flushed — `flush` happens on new conversation create; user message is `add` then agent runs then assistant `add` then `commit`. If `run_agent` throws, the request session rolls back on FastAPI `get_db` finally... actually `get_db` only `close()`s, it does **not** rollback on exception unless the middleware/session handles it. SQLAlchemy session `close()` typically rolls back uncommitted transactions. User message would not persist on LLM failure. Good.)

## 13.3 Redis "memory"

- **Chat cache:** SHA of message + repo id, 5 minutes, **first turn only**. This is a **result cache**, not conversational memory. It can return a previous answer **without** creating a new conversation — wait: cache hit returns `ChatResponse(**cached)` which includes a `conversation_id` from the **first** cached run. A second user asking the same question gets the same conversation id in the JSON. That can attach them to someone else's conversation id in the client if they then send a follow-up... Cache is keyed by repo+message not user. **Possible cross-user conversation_id leak of cached payload** (answer text + sources + conversation uuid). Auth still required; the cached body is from a previous successful chat. Follow-up with that id would load that conversation if `user_id` matches... `chat()` checks `conversation.user_id != user_id` and then **resets conversation to None** (creates new). So a cached `conversation_id` from user A, used by user B, would fail the ownership check and start a **new** conversation, but user B still **saw** user A's answer from cache. Same-question cache sharing across users on the same repo. Privacy consideration for private repos.

- **Rate limit keys:** not memory.

## 13.4 How memory flows into the graph

```
Postgres messages (prior turns)
    → format_history → state["history"] string
User's new question
    → state["question"]
invoke()
    → retrieve.rewrite_query uses both
    → compose_answer prompt includes both
    → source_check does not include history
Final answer
    → new Postgres assistant message
    → NOT written back into LangGraph state store (there is none)
```

---

# Section 14 — Streaming

## 14.1 LangGraph streaming

`stream()`, `astream()`, `astream_events()`, `astream_log()` — **not called**.

## 14.2 Token streaming

`client.chat.completions.create` is non-streaming. No `stream=True`.

## 14.3 HTTP streaming

Chat route returns a JSON `ChatResponse`. No SSE, no WebSocket.

## 14.4 What the user sees instead

`chatBusy` boolean in `App.tsx` / `ChatPanel.tsx` / `RightSidebar.tsx` ("Generating response"). The UI waits for the full HTTP response.

## 14.5 How you would add it (context for interviews, not implemented)

LangGraph: `for event in app.stream(state): ...` yielding node outputs. Token-level: replace `chat_completion` with a streaming OpenAI call and SSE from FastAPI. That would require **not** using a single `invoke()` in `run_agent`. None of this is in the repo.

---

# Section 15 — Error Handling

## 15.1 Around LangGraph `invoke`

**There is no try/except around `app.invoke()`.** If a node raises, FastAPI returns 500, `RequestIdMiddleware` logs `request_error`. Conversation commit does not happen.

## 15.2 Inside nodes

| Location | Handling |
| -------- | -------- |
| `node_retrieve` | `try/finally` **only** for `db.close()`. Retrieval errors propagate. |
| `node_answer` | Broad `except Exception` on LLM → snippet fallback (still cited). |
| `node_source_check` | Broad `except` on retry LLM → `ok = False` → refusal. |
| `rewrite_query` | `except` → `_fallback_rewrite`. |
| `vector_search` | embed fail → `[]`. |
| `keyword_search` | FTS fail → ILIKE. |
| `llm_rerank` | fail → heuristic. |
| `chat_repo` | Rate limit 429; repo 404; cache JSON errors ignored (`cache_get` returns None). |

## 15.3 Retries

| Kind | Present? |
| ---- | -------- |
| LangGraph retry policy / node retry | No |
| LLM HTTP retries (tenacity) | No |
| Grounding retry (one extra LLM rewrite) | **Yes**, in `source_check` |
| Retrieve-on-fail loop | No |
| Indexing `index_max_attempts` | Yes, but **worker**, not graph |

## 15.4 Fallback nodes

No dedicated fallback **node**. Fallbacks are in-function:

- Empty chunks → static string in `node_answer`.
- LLM down → dump top chunk.
- Ungrounded → `ungrounded_refusal`.

## 15.5 Dead ends

The graph cannot dead-end: every node has an outgoing edge to the next node or `END`. A **logical** dead end is GRAPH/CODE with no extracted symbol (branch skipped, RAG-only). HISTORY with empty DB still returns a `_git_recent` chunk ("No git history indexed yet.").

## 15.6 Recovery logic

Ungrounded refusal lists up to 5 candidate files as **not confirmed evidence**, so the UI still has paths to click while the model refuses to pretend.

`ensure_citations_footer` in `query_utils.py` is a **legacy helper** that appends a `Sources:` dump. `source_check` **does not call it**. Tests still cover it. Footer-only answers are explicitly **not** grounded (`test_footer_alone_is_not_grounded_success`).

---

# Section 16 — Complete Execution Walkthrough

**Request:** first message in a completed index of a Flask-like repo (matches default UI + `eval_questions.json`):

> "Where is the application factory defined?"

Assume: authenticated user, rate limit OK, cache miss, empty history, `create_app` lives in `src/flask/app.py` lines 10–40, `LLM_API_KEY` set, `rerank_mode=heuristic`.

---

### State 0 — `run_agent` seed (before START)

```python
{
  "repository_id": "11111111-1111-1111-1111-111111111111",
  "question": "Where is the application factory defined?",
  "history": "",
  "kinds": [],
  "chunks": [],
  "notes": [],
  "answer": "",
  "sources": [],
  "citation_ok": False,
}
```

---

### Node 1 — `classify` / `node_classify`

`classify_query` lowercases the question.

- Always appends `RAG`.
- `"defined"` matches CODE triggers (`"defined"` in `("where is", "defined", "definition", ...)`).
- No HISTORY/DOCS/GRAPH keywords.

**Return:** `{"kinds": ["RAG", "CODE"]}`

**State 1:**

```python
kinds == ["RAG", "CODE"]
# all other fields unchanged
```

---

### Node 2 — `retrieve` / `node_retrieve`

Opens `SessionLocal()`.

`rewrite_query`:

- LLM returns e.g. `rewrites: ["application factory defined", "create_app"]`, `symbols: ["create_app"]`.
- Original question prepended → `rewrites[:3]` ≈  
  `["Where is the application factory defined?", "application factory defined", "create_app"]`.
- `hint = "create_app"`.

**Always RAG:** three `search_code` → three `hybrid_retrieve` → each embeds the rewrite, searches pgvector/FTS/symbols, RRF+rerank to 10. Then **outer** `reciprocal_rank_fusion(rag_lists, limit=10)` so the fused RAG set is 10 chunks. `src/flask/app.py:10-40` should rank first (`test_rrf_prefers_relevant_over_noise` encodes this intent).

**CODE:** `search_symbol(db, repo, "create_app", limit=8)` plus `find_references`. Notes e.g. `references:create_app:[{source, target, type}, ...]`.

No GRAPH/HISTORY/DOCS.

**Return:** `{chunks: [... ~10–18 dicts ...], notes: ["references:create_app:..."]}`

**State 2:** `chunks` reducer: `[] + that list`. `notes` similarly.

---

### Node 3 — `compose_answer` / `node_answer`

Dedup by `file:start:end`, slice to `context_chunks` (10). Append `_agent_notes` with references string.

`build_answer_messages` → `chat_completion(..., temperature=0.0)`.

Example model output:

```
The application factory is `create_app` in src/flask/app.py:10-40.
It constructs the Flask app instance and registers configuration.
```

**Return:** `{answer: <that text>, sources: [{file: "src/flask/app.py", start_line: 10, end_line: 40, snippet: "def create_app..."}, ...]}`

`citation_ok` still False (not set).

**State 3:** answer + sources filled.

---

### Node 4 — `source_check` / `node_source_check`

`is_grounded`:

- Answer has no insufficiency phrase.
- `_strip_sources_footer` leaves body intact.
- `CITATION_RE` matches `src/flask/app.py:10-40`.
- File is in allowed set → **True**.

**Return:** `{citation_ok: True, answer: <unchanged>}`

No second LLM call.

**State 4 (final / END):**

```python
{
  "repository_id": "11111111-...",
  "question": "Where is the application factory defined?",
  "history": "",
  "kinds": ["RAG", "CODE"],
  "chunks": [ ... retrieved ... ],
  "notes": ["references:create_app:..."],
  "answer": "The application factory is `create_app` in src/flask/app.py:10-40. ...",
  "sources": [ { "file": "src/flask/app.py", "start_line": 10, "end_line": 40, "snippet": "..." }, ... ],
  "citation_ok": True,
}
```

---

### After the graph

`run_agent` returns `(answer, sources)`.

`chat()` inserts assistant `Message`, commits.

`chat_repo` builds `ChatResponse`, `cache_set` because `conversation_id` was None.

Frontend stores `conversation_id`, renders answer, citation chips call `onOpenCitation` → fetch file → highlight lines 10–40.

---

### Alternate ending — ungrounded

If the model answered "It's in the core module." with no path:

1. `is_grounded` False.
2. No git chunks → no commit exception.
3. `build_source_check_messages` + LLM rewrite.
4. If rewrite still has no citation:  
   `I could not verify that answer from the indexed sources.`  
   plus candidate file list.
5. `citation_ok` False. User still sees candidates.

---

# Section 17 — Interview Questions

Answers are about **this** implementation, not generic LangGraph.

---

### Why did you choose LangGraph over a LangChain chain?

A chain would hide classify / retrieve / answer / verify as nested runnables. We needed **named stages** we can log (`retrieve ... ms=`, `citation_ok=`), unit-test (`classify_query`, `is_grounded`) independently, and later split into parallel retrieve nodes (`operator.add` is already on `chunks`). We did **not** need LCEL; we don't even import `langchain-core` in app code. LangGraph is the control-flow layer; OpenAI SDK is the model layer.

### Why is `StateGraph` appropriate here?

The working memory is not a chat message list. It is **repo-scoped retrieval state**: `kinds`, `chunks`, `notes`, `citation_ok`. `MessagesState` would force us to stuff JSON into messages. Custom `TypedDict` + reducers matches a RAG pipeline.

### Is this an agent?

It is a **grounded RAG orchestrator** packaged under `app/agents`. It does not choose tools at runtime. If an interviewer hears "agent" they should hear: "four-node StateGraph, not ReAct."

### How does your conditional routing work?

It doesn't at the graph level. `classify_query` fills `kinds`; `node_retrieve` uses `if "CODE" in kinds`. Edges are unconditional. We traded skip-logic efficiency for "always retrieve semantically, then add specialist channels."

### How is state persisted?

LangGraph state is **not** persisted. Postgres `conversations` / `messages` persist the user-visible transcript. Redis caches **first-turn** identical questions for 300 seconds. Restarting the backend loses no chat history (it's in Postgres) but loses the compiled graph singleton (rebuilt on first request) and Redis cache.

### Why `operator.add` on `chunks` if retrieve is a single node?

Forward-compatible fan-in. Today retrieve extends Python lists internally and returns once. Parallel `Send` workers would concatenate automatically. Cost: accidental double-append if a future node also returns `chunks`.

### Why open a new DB session inside retrieve?

`invoke()` has no configurable context. FastAPI's `Session` is not in `AgentState` (and putting a live session in state is a bad idea). Retrieve uses `SessionLocal()` and always closes it. Conversation writes use the request session. Two connections per chat.

### How do you prevent hallucination?

Three layers: (1) `ANSWER_SYSTEM` "only provided source context"; (2) regex `CITATION_RE` + allowed file set (`is_grounded`); (3) refusal that must not be satisfied by a fake `Sources:` footer (`_strip_sources_footer`). Git answers get a `commit:` escape hatch.

### How would you scale this?

Horizontally: more uvicorn workers, each with its own compiled graph (stateless). Bottleneck is **per-request LLM + 3× embeddings**, not LangGraph. Scale Postgres (HNSW on embeddings — not created today), cap rewrites, skip RAG for pure HISTORY questions via **actual** conditional edges, add a checkpointer only if you need resume. Cache embeddings of rewrites. Don't put the graph in the worker process.

### What happens if the LLM is down?

Compose falls back to quoting the top chunk with a real `path:start-end`, so source_check usually passes. The user sees `(LLM unavailable: ...)`. Rewrite falls back to regex. Chat is degraded, not dark, if retrieve still found chunks.

### Why not `ToolNode`?

The model must not freely `read_file` the whole repo (context + cost + prompt injection via comments). Retrieval policy is **code**, not the model's tool choice.

### How do follow-up questions work?

Client sends `conversation_id`. `chat()` loads last 6 turns into `history`. Rewrite and compose see it; symbol hints can come from history (`who calls that?`). Source-check retry does **not** see history.

### How is this tested?

`test_agent_v2.py` and `test_rag_upgrade.py` test **helpers**, not `build_graph().invoke()`. There is no compiled-graph integration test. `eval_questions.json` is a contract for live eval gated by `RUN_RAG_EVAL`.

### Why compile as a singleton?

Nodes are pure with respect to process config. Compiling per request would add latency for no benefit. Settings are read **inside** nodes via `get_settings()`, so changing env still requires process restart (`lru_cache` on settings).

---

# Section 18 — Improvement Opportunities

Staff-engineer review of **this** graph, not generic RAG advice.

## Bottlenecks

1. **Three hybrid retrieves per question** (rewrite cap 3). Each hybrid retrieve embeds the query. First-turn latency is dominated by `3 × (embed + 4 searches + rerank)` plus 1–2 chat completions.
2. **Always-on RAG** even for "highest churn" HISTORY questions. Extra embed+SQL with little value.
3. **Synchronous FastAPI + `invoke()`** holds a worker for the full LLM round trip. No streaming, no background job for chat.
4. **No pgvector HNSW/IVF** in `init_db` — vector search is likely sequential scan as repos grow.
5. **Second RRF in retrieve** after each hybrid already reranked to `context_chunks`. Duplicate ranking work.

## Unnecessary complexity

1. `operator.add` without parallel nodes — documents a future that never landed.
2. Dead tools (`read_file`, `graph_summary`, `get_file_structure`, `tool_knowledge_tree`) imply a ReAct design that was abandoned.
3. `langchain-core` in requirements with zero app imports.
4. `ensure_citations_footer` is unused in the live path but still tested — two citation philosophies in one module.
5. `citation_ok` from empty-context `node_answer` is overwritten by source_check because "could not find" matches `is_grounded`. The flag is not a reliable metric.

## Missing checkpoints

If you add interrupt/HITL or want to debug production runs, compile with a Postgres checkpointer keyed by `conversation_id`. Today you cannot replay `kinds`/`chunks` for a past message; you only stored the final answer and sources JSONB, **not** the retrieve set used for grounding (sources are a truncated, notes-stripped view).

## Scalability

- Inject `db` via `config["configurable"]` instead of `SessionLocal()` per retrieve (connection pool churn, can't join to the request transaction if you ever need to).
- Conditional edges: skip RAG for HISTORY-only keyword hits.
- Bound `chunks.extend` from CODE/HISTORY so `node_answer` slice isn't hiding a 80-chunk prompt build before slice... actually notes and extras are built in retrieve then sliced in answer — **LLM still only sees 10**, but retrieve **paid** for extra SQL.
- Per-user LLM budget; chat cache currently shares answers across users.

## Observability gaps

- Node logs have no `request_id` (middleware has it; retrieve logger doesn't call `get_request_id()`).
- No token counts, no retrieve-hit metrics in `IndexingJob` style `metrics` JSON.
- `citation_ok` not in HTTP response or Message row — you cannot dashboard grounding rate without scraping logs.
- No LangGraph Studio / tracing exporter.

## Retry improvements

- Tenacity around `chat_completion` for 429/502 from OpenRouter.
- If source_check fails, **one retrieve retry** with `rewrite_query` biased to cited filenames from the refusal candidates — currently we only rewrite prose.
- Don't treat empty-context insufficiency as `citation_ok=true` in source_check; short-circuit source_check when compose already refused.

## Memory improvements

- Pass `thread_id=conversation.id` into a checkpointer if you want mid-graph resume.
- Include history in source_check messages.
- Cache key should include `user_id` (and maybe `commit_hash`) to avoid cross-user cache and stale answers after re-index (cache is **not** invalidated on index complete).
- Increase `max_turns` or summarize old turns; 6×800 chars is small for debugging sessions.

## Testing gaps

- **Zero tests compile the graph.** A Fake LLM + in-memory chunks would lock the edge list.
- No test that GRAPH with no symbol is a no-op.
- No test that `operator.add` doesn't duplicate if retrieve is invoked twice (it isn't).
- No TestClient test for `POST /chat` auth/cache/rate-limit interaction with the agent.
- Live eval (`RUN_RAG_EVAL`) is a placeholder assert.

## Production-ready recommendations (priority)

1. **Invalidate or namespace chat cache** by `user_id` + `repository.commit_hash`.
2. **Log `get_request_id()`** in retrieve/llm/citation lines; persist `citation_ok` and chunk ids on `Message`.
3. **Add `add_conditional_edges`** after classify: HISTORY-only skip extra RAG; or keep RAG but drop extra rewrites when kinds == {HISTORY}.
4. **Integration test** `build_graph().invoke` with monkeypatched `chat_completion` and `search_code`.
5. **Pass session via LangGraph config** so retrieve doesn't open a second connection.
6. **Stream tokens** (product) or at least `graph.stream` node events to SSE for "Retrieving… / Writing… / Checking citations…".
7. **Attach snippet bodies** to `build_source_check_messages` so the retry model can actually entail claims.
8. **Delete or wire dead tools**; don't leave a fake agent surface.
9. Add HNSW index on `code_chunks.embedding`.
10. Bound and metric the CODE `chunks.extend` path so context isn't random first-10 after dict insertion order.

---

# Section 19 — File-by-File LangGraph Cheat Sheet

Read this table top-to-bottom the night before an interview.

| File | Why it exists | Read first? |
| ---- | ------------- | ----------- |
| `backend/app/agents/workflow.py` | The graph: state, nodes, compile, invoke, chat persistence wrapper. | **Yes — start here** |
| `backend/app/agents/query_utils.py` | Classification, rewrite, citation regex, grounding, history format. | **Yes — second** |
| `backend/app/rag/prompts.py` | The two system prompts that define product behavior. | **Yes — third** |
| `backend/app/api/routes.py` (`chat_repo`) | HTTP entry, auth, rate limit, Redis cache. | Yes |
| `backend/app/agents/tools.py` | Retrieve facades; note which functions are dead. | Yes |
| `backend/app/rag/retriever.py` | What "RAG" means in this repo (hybrid + RRF + rerank). | Yes |
| `backend/app/rag/embeddings.py` | The only LLM/embed client. | Yes |
| `backend/app/rag/fusion.py` | RRF formula and dedup key. | If asked about ranking |
| `backend/app/rag/reranker.py` | Heuristic vs optional LLM rerank. | If asked about quality |
| `backend/app/services/git_history_service.py` (`get_git_history_context`) | HISTORY channel chunk shapes (`_git_churn`, `commit:sha`). | If asked about git Q&A |
| `backend/app/services/graph_service.py` (`expand_graph_neighbors`) | CODE/GRAPH neighborhood expansion. | If asked about "graph RAG" |
| `backend/app/models/__init__.py` (`Conversation`, `Message`) | Real memory (Postgres). | If asked about state |
| `backend/app/schemas.py` (`ChatRequest`, `ChatResponse`, `SourceRef`) | API contract. | Skim |
| `backend/app/config.py` | Models, `context_chunks=10`, `retrieve_candidates=40`, `rerank_mode`. | Skim |
| `backend/app/services/cache.py` | First-turn Redis cache. | If asked about perf/privacy |
| `backend/app/database.py` | `SessionLocal` used by retrieve's `_db()`. | If asked about sessions |
| `backend/app/agents/__init__.py` | Lazy import of chat/run_agent. | Skip unless cycles |
| `backend/requirements.txt` | `langgraph==0.2.60`. | Cite version |
| `backend/tests/test_agent_v2.py` | Classify + citation unit tests. | If asked "how do you test?" |
| `backend/tests/test_rag_upgrade.py` | Grounding, rewrite fallback, RRF. | Same |
| `backend/tests/eval_questions.json` | Gold questions for citation paths. | Same |
| `frontend/src/api.ts` | `POST .../chat`. | If asked about clients |
| `frontend/src/App.tsx` (`sendChat`) | conversationId, busy state. | Same |
| `backend/app/services/rag_service.py` | **Indexing** chunks/embeddings — not runtime graph, but retrieve reads this data. | If asked "where do chunks come from?" |
| `worker/worker.py` / `indexing_pipeline.py` | Fills the stores the graph queries. Not LangGraph. | Context only |
| `frontend/src/GraphView.tsx` | **Not LangGraph.** Dependency visualization. | Do not confuse |

---

## 30-second oral summary

> Chat is a compiled LangGraph `StateGraph` with four sequential nodes: classify, retrieve, compose, source-check. It is a RAG orchestrator, not a tool-calling agent. Classification is keyword-based and does not branch the graph. Retrieve always runs hybrid multi-query RAG and may add symbol, dependency-graph, git-history, or markdown channels. The LLM is OpenRouter via the OpenAI SDK at temperature 0. Answers must cite `path:start-end` or we rewrite once and then refuse. Conversation history lives in Postgres, not a LangGraph checkpointer. The only `invoke()` is synchronous behind `POST /api/repositories/{id}/chat`.

---

## Debugger map (where to put breakpoints)

| Symptom | Breakpoint |
| ------- | ---------- |
| Wrong retrieval mix | `classify_query`, then `kinds` in `node_retrieve` |
| Bad search queries | `rewrite_query` return value |
| Missing file in context | `hybrid_retrieve` lists, then RRF, then `node_answer` dedup slice |
| Hallucinated path | `is_grounded` / `CITATION_RE` |
| Slow chat | `node_retrieve` timer log; count of `rewrites` |
| Empty answer | `chat_completion` return; empty `context_chunks` branch |
| Follow-up ignores prior | `format_history` / `conversation_id` on the request |
| Cache serving stale/wrong user | `chat_cache_key` / `cache_get` in `chat_repo` |

This is the complete LangGraph architecture of this repository as of the code in `backend/app/agents/workflow.py` and its call graph.
