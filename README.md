# GitHub Repository AI Assistant

Docker-first platform that indexes a public GitHub repository and answers questions with file/line citations.

## Host requirements

- Docker Desktop / Docker Engine only
- No local Postgres, Redis, Node, or Git install is required for running the stack

Databases run **only** as Compose services (`postgres` with pgvector, `redis`).

## Quick start

```bash
cp .env.example .env
# set LLM_API_KEY (OpenRouter) in .env

docker compose up --build
```

Services:

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API / docs | http://localhost:8000/docs |
| Postgres (debug only) | localhost:5432 |
| Redis (debug only) | localhost:6379 |

App containers connect via Compose DNS (`postgres`, `redis`), not localhost.

## Flow

1. Paste a public GitHub URL in the UI and click **Analyze**
2. Worker clones, parses (Python/JS/TS), builds graph + knowledge tree, chunks, embeds
3. Explore tree/graph, open sources, chat with citations

## Environment

See [.env.example](.env.example). Important:

- `DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/app_db`
- `REDIS_URL=redis://redis:6379/0`
- `LLM_BASE_URL=https://openrouter.ai/api/v1`

## Tests (inside backend container)

```bash
docker compose exec backend pytest -q
```
