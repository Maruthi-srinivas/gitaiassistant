from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import uuid

app = FastAPI()

# In-memory store for MVP
repositories: Dict[str, Dict] = {}


class RepoIn(BaseModel):
    url: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/repositories", status_code=201)
def create_repository(data: RepoIn):
    repo_id = str(uuid.uuid4())
    repositories[repo_id] = {
        "id": repo_id,
        "url": data.url,
        "status": "created",
    }
    return repositories[repo_id]


@app.get("/api/repositories")
def list_repositories():
    return list(repositories.values())


@app.post("/api/repositories/{repo_id}/index")
def index_repository(repo_id: str):
    repo = repositories.get(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    # Enqueue work to worker (stub) — update status for now
    repo["status"] = "queued"
    return {"job_id": str(uuid.uuid4()), "status": repo["status"]}
