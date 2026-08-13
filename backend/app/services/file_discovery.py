from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

from git import Repo

from app.config import get_settings
from app.services.github_service import ParsedRepoUrl

logger = logging.getLogger(__name__)

IGNORE_DIRS = {
    ".git",
    "node_modules",
    "target",
    "build",
    "dist",
    "venv",
    ".venv",
    "__pycache__",
    ".idea",
    ".vscode",
    "coverage",
    "vendor",
    ".next",
    ".turbo",
    "eggs",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}

IGNORE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".jar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".class",
    ".pyc",
    ".pyo",
    ".o",
    ".a",
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
}

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
}


def repo_local_path(repo_id: str) -> Path:
    settings = get_settings()
    path = Path(settings.data_dir) / str(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clone_repository(parsed: ParsedRepoUrl, dest: Path) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    url = parsed.clone_url
    if settings.github_token:
        url = f"https://{settings.github_token}@github.com/{parsed.owner}/{parsed.name}.git"
    logger.info("Cloning %s into %s", parsed.html_url, dest)
    repo = Repo.clone_from(url, dest, depth=1)
    return repo.head.commit.hexsha


def pull_or_fetch(dest: Path) -> tuple[str, str]:
    """Return (old_commit, new_commit). Raises RuntimeError with a clear message on failure."""
    try:
        repo = Repo(dest)
        old = repo.head.commit.hexsha
        origin = repo.remotes.origin
        # Shallow clones need deepen/unshallow for reliable updates
        try:
            origin.fetch(prune=True)
        except Exception:
            # Retry with unshallow if this was a depth=1 clone
            repo.git.fetch("--unshallow", "origin")
        try:
            branch = repo.active_branch.name
        except TypeError:
            # Detached HEAD — use remote HEAD default branch
            branch = repo.git.symbolic_ref("refs/remotes/origin/HEAD").replace("refs/remotes/origin/", "")
        repo.git.reset("--hard", f"origin/{branch}")
        new = repo.head.commit.hexsha
        return old, new
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Incremental git fetch failed ({exc}). Use Analyze for a full re-index."
        ) from exc


def changed_files(dest: Path, old_commit: str, new_commit: str) -> list[str]:
    repo = Repo(dest)
    diffs = repo.commit(old_commit).diff(new_commit)
    paths: list[str] = []
    for d in diffs:
        path = d.b_path or d.a_path
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def detect_language(path: Path) -> str | None:
    return LANG_BY_EXT.get(path.suffix.lower())


def should_ignore(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = set(rel.parts)
    if parts & IGNORE_DIRS:
        return True
    name = path.name.lower()
    if any(name.endswith(suf) for suf in IGNORE_SUFFIXES):
        return True
    if path.is_file() and path.stat().st_size > 1_000_000:
        return True
    return False


def discover_files(root: Path) -> list[dict]:
    results: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        current = Path(dirpath)
        for filename in filenames:
            path = current / filename
            if should_ignore(path, root):
                continue
            language = detect_language(path)
            if language is None:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            results.append(
                {
                    "path": rel,
                    "language": language,
                    "size": len(content.encode("utf-8")),
                    "hash": digest,
                    "content": content,
                }
            )
    return results
