# Production Readiness & Development Roadmap

**Current State:** Backend `v5.0.0` / Frontend `v1.0.0` (Feature-Complete MVP)  
**Goal:** Transition from a Dockerized single-node MVP to a multi-tenant, scalable, production-grade SaaS application.

---

## 0. Current Baseline (Completed Milestones: V1 → V5)

Before tracking pending work, the following capabilities are **already implemented** in the repository:

- **V1 — Core Platform:** Repository cloning, AST parsing (Python, JS, TS), call/dependency graph, hierarchical knowledge tree, pgvector embeddings, and RAG chat with file/line citations.
- **V2 — Multi-Language, Git Analytics & UI Overhaul:** Added Java, Go, and C++ parsers; Git commit history, file churn, and co-change analysis; branch-aware indexing; 3-pane workspace UI with Dagre graph layout.
- **V3 — Advanced Agentic RAG:** LangGraph workflow (`0.2.60`) with multi-query retrieval, PostgreSQL full-text search (`tsvector` + GIN index) + vector search, Reciprocal Rank Fusion (RRF), cross-encoder / heuristic reranking, and strict citation grounding verification.
- **V4 & V5 — Auth, Private Repos & Resilience:** JWT registration/login (`/api/auth/*`), multi-tenant user ownership (`owner_user_id`), private GitHub repository token support, request-ID middleware (`X-Request-ID`), Redis worker retry + Dead Letter Queue (`:processing` and `:dlq`), and GitHub Actions CI (`pytest` + `npm run build`).

---

## Stage 1: Security, Auth & Secrets Hardening (P0 — Completed)

### 1.1 Encrypt User GitHub Tokens at Rest (Completed)
- **Implemented:** `User.github_token` is encrypted at rest using `cryptography.fernet.Fernet` (`enc:v1:...` prefix) in `backend/app/crypto.py` via `ENCRYPTION_KEY`, and decrypted only in-memory inside `repository_service.py` and `indexing_pipeline.py`.
- **Implemented:** `/api/auth/me` only exposes `has_github_token: bool`, never raw tokens.

### 1.2 Enforce Strong Production Secrets & JWT Lifecycle (Completed)
- **Implemented:** `validate_runtime_security()` in `backend/app/config.py` fails fast on API and worker startup when `ENVIRONMENT=production` if `JWT_SECRET` is weak/default (<32 chars) or `ENCRYPTION_KEY` is missing.
- **Implemented:** JWT access tokens include `jti` and `iat` claims (`24h` default TTL) with Redis-backed `jti` revocation on `POST /api/auth/logout` (`backend/app/auth.py` and `frontend/src/api.ts`).
- **Implemented:** Password complexity validation (`validate_password_strength`: 8–128 chars, uppercase, lowercase, digit) enforced on registration. (SMTP email verification / reset deferred until an email provider is configured.)

### 1.3 Sanitize Git Clone URLs & Subprocess Logs (Completed)
- **Implemented:** `clone_repository`, `checkout_branch`, and `pull_or_fetch` in `backend/app/services/file_discovery.py` pass credentials transiently via `-c http.https://github.com/.extraheader=Authorization: Basic ...` so `.git/config` on disk only stores clean `https://github.com/{owner}/{name}.git` URLs.
- **Implemented:** `redact_secrets()` strips GitHub PATs, basic auth headers, and embedded URL credentials before persisting `IndexingJob.error` / `Repository.error` or emitting logs.

---

## Stage 2: Database Migrations, Vector Indexing & Storage (P0 — Data Layer)

### 2.1 Replace Runtime `_ensure_columns()` with Alembic Migrations
- **Current state:** Schema changes are applied via `Base.metadata.create_all()` and raw `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements inside `backend/app/database.py` (`_ensure_columns()`).
- **Pending work:**
  - Initialize **Alembic** in `backend/alembic/`.
  - Generate an initial baseline migration representing the V5 schema.
  - Run `alembic upgrade head` in an init container or release step instead of mutating schema on every FastAPI worker startup.

### 2.2 Add pgvector HNSW Index on `code_chunks.embedding`
- **Current state:** `code_chunks` has a GIN index on `search_tsv`, but vector similarity queries perform sequential scans over `code_chunks.embedding`.
- **Pending work:**
  - Create an HNSW index for fast approximate nearest neighbor search:
    ```sql
    CREATE INDEX CONCURRENTLY ix_code_chunks_embedding_hnsw
    ON code_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
    ```
  - Add composite B-tree indexes on `code_chunks(repository_id, file_id)` and `symbols(file_id, name)`.

### 2.3 Offload Raw File Contents from PostgreSQL
- **Current state:** `FileRecord.content` stores the entire source text of every indexed file in PostgreSQL (`files.content`), bloating database memory and backups.
- **Pending work:**
  - Store source blobs in object storage (S3 / MinIO / GCS) keyed by `(repository_id, commit_hash, content_sha256)` or read directly from the cloned git worktree.
  - Keep only metadata (`path`, `language`, `size`, `hash`) in the `files` table.

### 2.4 Cache Invalidation on Re-Index
- **Current state:** `backend/app/services/cache.py` caches chat responses (`cache:chat:{repo_id}:{hash}`) for 300s, and does not invalidate when a repository is re-indexed or switches branches.
- **Pending work:**
  - Include `commit_hash` in the cache key (`cache:chat:{repo_id}:{commit_hash}:{hash}`) or purge `cache:chat:{repo_id}:*` on indexing job completion.

---

## Stage 3: API Governance, Rate Limiting & Cost Controls (P1 — Abuse Prevention)

### 3.1 API Rate Limiting & Concurrency Quotas
- **Current state:** No rate limiting exists on `/api/auth/*`, `/api/repositories/{id}/index`, or `/api/repositories/{id}/chat`.
- **Pending work:**
  - Add Redis-backed rate limiting (e.g., `slowapi` or custom token-bucket middleware):
    - Auth endpoints: e.g., 10 req/min per IP.
    - Chat endpoint: e.g., 20 req/min per user.
    - Indexing jobs: max 2 concurrent active jobs per user.

### 3.2 LLM Token Accounting & Per-User Quotas
- **Current state:** Every chat turn and knowledge-tree enrichment calls OpenRouter (`LLM_API_KEY`) without tracking token consumption or cost per user.
- **Pending work:**
  - Record `prompt_tokens`, `completion_tokens`, and model name from OpenRouter responses into a `usage_logs` table.
  - Enforce daily/monthly token budgets per user tier.

### 3.3 API Versioning (`/api/v1`)
- **Current state:** Routes are mounted under unversioned `/api/...` (`backend/app/api/routes.py`).
- **Pending work:**
  - Mount versioned routers under `/api/v1/...` while keeping `/api/health` for load balancer probes.

---

## Stage 4: Real-Time UX & Streaming (P1 — User Experience)

### 4.1 Server-Sent Events (SSE) for Indexing Progress
- **Current state:** The React frontend polls `GET /api/repositories/{id}/index-status` every 2 seconds (`frontend/src/App.tsx`).
- **Pending work:**
  - Have the worker publish progress transitions (`CLONING` → `PARSING` → `EMBEDDING` → `COMPLETED`) to Redis Pub/Sub.
  - Expose `GET /api/repositories/{id}/index-stream` (SSE) in FastAPI and subscribe via `EventSource` in the UI.

### 4.2 Streaming Chat Responses (Token-by-Token + Agent Step Events)
- **Current state:** `POST /api/repositories/{id}/chat` blocks until the entire LangGraph workflow (`plan` → `retrieve` → `answer` → `verify`) finishes.
- **Pending work:**
  - Add a streaming chat endpoint using LangGraph `.astream_events()` over SSE.
  - Stream intermediate agent status (`"Searching symbols..."`, `"Reranking 18 chunks..."`, `"Verifying citations..."`) followed by token-by-token answer generation.

---

## Stage 5: Worker Scalability & Indexing Pipeline Hardening (P2 — Scale)

### 5.1 Stale / Orphaned Job Recovery (Visibility Timeout)
- **Current state:** `indexing_worker.py` moves items from `queue` to `queue:processing` and re-queues leftovers only when the single worker process restarts. If multiple workers run, a crashed worker leaves jobs stuck in `processing` until another restart.
- **Pending work:**
  - Track heartbeats per `job_id` in Redis with a TTL, or migrate the custom `BLPOP` loop to **ARQ / Celery / RQ** with built-in visibility timeouts and scheduled retries.

### 5.2 Shared Storage for Multi-Node Workers
- **Current state:** Backend and worker share a local Docker bind mount (`./data:/data`) for cloned git repositories.
- **Pending work:**
  - Decouple API servers from `/data` by having file-content and git-blame reads served from Postgres/S3, or mount a shared network filesystem (AWS EFS / NFS) only across worker nodes.

### 5.3 Parallel File Parsing & Batch Embedding Backpressure
- **Current state:** Parsing runs sequentially in a single thread per job; large repositories can hit timeouts or rate limits during embedding generation.
- **Pending work:**
  - Parse files concurrently using `ProcessPoolExecutor` (bounded by CPU cores).
  - Add exponential backoff + jitter on OpenRouter embedding/chat HTTP `429` / `5xx` responses in `backend/app/rag/embeddings.py`.
  - Enforce repository size and file-count guardrails before cloning (check repo `size` from GitHub API metadata).

---

## Stage 6: Infrastructure, Observability & Testing (P2 — Operations)

### 6.1 Production Web Server & Ingress Configuration
- **Current state:**
  - `frontend/Dockerfile` serves static assets using `npx serve -s dist` on Node.js.
  - `backend/Dockerfile` runs a single `uvicorn` process.
  - `VITE_API_BASE` is not parameterized as a Docker build argument.
- **Pending work:**
  - Replace `serve` in `frontend/Dockerfile` with **Nginx** (gzip/brotli compression, cache headers, SPA fallback, and reverse proxy for `/api`).
  - Run backend with Gunicorn + Uvicorn workers (`gunicorn -k uvicorn.workers.UvicornWorker -w 4`).
  - Add TLS termination (Traefik / Nginx / Cloud Load Balancer) and strict CORS origins.

### 6.2 Structured Logging, Tracing & Metrics
- **Current state:** `RequestIdMiddleware` and `indexing_worker.py` emit JSON for a few events, while the rest of the codebase uses plain-text `logging.info` without `request_id` correlation.
- **Pending work:**
  - Configure a unified JSON log formatter across all modules that automatically injects `request_id`, `user_id`, `repo_id`, and `job_id` from contextvars.
  - Instrument FastAPI, SQLAlchemy, and httpx with **OpenTelemetry** + Prometheus `/metrics` endpoint.
  - Add an admin endpoint or dashboard to inspect and replay Dead Letter Queue (`index_jobs:dlq`) items.

### 6.3 End-to-End & Integration Test Suite
- **Current state:** Unit tests cover parsers, auth helpers, URL parsing, and RAG utilities (`backend/tests/`), and CI builds the frontend.
- **Pending work:**
  - Add FastAPI `TestClient` integration tests against an ephemeral Postgres (`pgvector`) + Redis service container in `.github/workflows/ci.yml`.
  - Automated RAG evaluation gate running `backend/tests/eval_questions.json` to track retrieval recall and citation grounding accuracy across PRs.
  - Add Playwright E2E smoke tests for Login → Analyze Repo → Knowledge Tree / Graph → Chat.

---

## Stage 7: Repository Cleanup & Documentation Alignment (P3 — Hygiene)

- **Remove deprecated stubs:** Delete `worker/worker.py`, `worker/Dockerfile`, `worker/requirements.txt`, and root `parsers/__init__.py` (all superseded by `backend/app/workers/indexing_worker.py` and `backend/app/parsers/`).
- **Remove scratch files:** Remove or `.gitignore` scratch files like `apidetails.txt` and root image assets.
- **Update documentation:**
  - Update `README.md` to document authentication, private repo support, Go/C++ language support, and full `.env` variables.
  - Update `PROJECT_ARCHITECTURE_HANDBOOK.md` to reflect `v5.0.0` (mark Auth, CI, DLQ, and FTS as implemented).

---

## Summary Checklist by Priority

| Priority | Stage | Key Deliverables |
| :--- | :--- | :--- |
| **P0 (Done)** | **1. Security & Auth** | Encrypted `github_token` at rest, JWT `jti` Redis revocation, transient Git HTTP auth headers & log redaction |
| **P0** | **2. Database & Storage** | Alembic migrations, pgvector HNSW index, offload `files.content` from Postgres, commit-scoped cache |
| **P1** | **3. API Governance** | Redis rate limiting, per-user LLM token budgets, `/api/v1` prefix |
| **P1** | **4. Real-Time UX** | SSE for indexing progress, token-by-token streaming for LangGraph chat |
| **P2** | **5. Worker Scale** | Worker heartbeats / ARQ or Celery, OpenRouter retry backoff, repo size limits |
| **P2** | **6. Infra & Observability** | Nginx frontend container, multi-worker Uvicorn, OpenTelemetry/Prometheus, Postgres+Redis in CI |
| **P3** | **7. Cleanup & Docs** | Remove deprecated `worker/` & `parsers/` stubs, sync `README.md` and architecture handbook |
