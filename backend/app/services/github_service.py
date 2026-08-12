from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.config import get_settings

GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass
class ParsedRepoUrl:
    owner: str
    name: str
    clone_url: str
    html_url: str


def parse_github_url(url: str) -> ParsedRepoUrl:
    cleaned = url.strip().rstrip("/")
    match = GITHUB_URL_RE.match(cleaned)
    if not match:
        raise ValueError("Invalid public GitHub repository URL")
    owner = match.group("owner")
    name = match.group("name")
    return ParsedRepoUrl(
        owner=owner,
        name=name,
        clone_url=f"https://github.com/{owner}/{name}.git",
        html_url=f"https://github.com/{owner}/{name}",
    )


def fetch_repo_metadata(owner: str, name: str) -> dict:
    settings = get_settings()
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
        if resp.status_code == 404:
            raise ValueError("Repository not found or not public")
        resp.raise_for_status()
        data = resp.json()
        return {
            "default_branch": data.get("default_branch") or "main",
            "private": bool(data.get("private")),
            "full_name": data.get("full_name"),
        }
