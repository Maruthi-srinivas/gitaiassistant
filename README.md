# GitHub Repository AI Assistant (MVP scaffold)

This repository contains a Phase 1 scaffold for the GitHub Repository AI Assistant.

Quick start (requires Docker Desktop):

```bash
docker-compose up --build
```

Services:
- `backend` — FastAPI app on port 8000
- `worker` — indexing/worker stub
- `redis` — task queue
- `postgres` — metadata DB (placeholder)
- `frontend` — static frontend stub on port 3000

Next steps: implement parsing, cloning, and indexing worker logic.
