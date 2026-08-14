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
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
}


def repo_local_path(repo_id: str) -> Path:
    settings = get_settings()
    path = Path(settings.data_dir) / str(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _clone_url(parsed: ParsedRepoUrl) -> str:
    settings = get_settings()
    if settings.github_token:
        return f"https://{settings.github_token}@github.com/{parsed.owner}/{parsed.name}.git"
    return parsed.clone_url


def clone_repository(parsed: ParsedRepoUrl, dest: Path, branch: str | None = None) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    url = _clone_url(parsed)
    depth = max(1, int(settings.git_clone_depth or 200))
    logger.info("Cloning %s into %s (depth=%s, branch=%s)", parsed.html_url, dest, depth, branch)
    kwargs: dict = {"depth": depth, "no_single_branch": True}
    if branch:
        kwargs["branch"] = branch
    repo = Repo.clone_from(url, dest, **kwargs)
    if branch:
        checkout_branch(dest, branch)
    return repo.head.commit.hexsha


def checkout_branch(dest: Path, branch: str) -> str:
    repo = Repo(dest)
    try:
        repo.git.fetch("origin", branch, depth=get_settings().git_clone_depth)
    except Exception:  # noqa: BLE001
        try:
            repo.git.fetch("origin", branch)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch branch %s: %s", branch, exc)
    try:
        repo.git.checkout("-B", branch, f"origin/{branch}")
    except Exception:
        repo.git.checkout(branch)
    return repo.head.commit.hexsha


def list_remote_branches(dest: Path) -> list[dict]:
    repo = Repo(dest)
    branches: list[dict] = []
    seen: set[str] = set()
    for ref in repo.remotes.origin.refs:
        name = ref.remote_head if hasattr(ref, "remote_head") else ref.name.replace("origin/", "")
        if name == "HEAD" or name in seen:
            continue
        seen.add(name)
        try:
            sha = ref.commit.hexsha
        except Exception:  # noqa: BLE001
            sha = None
        branches.append({"name": name, "commit_hash": sha})
    return sorted(branches, key=lambda b: b["name"])


def pull_or_fetch(dest: Path, branch: str | None = None) -> tuple[str, str]:
    """Return (old_commit, new_commit). Raises RuntimeError with a clear message on failure."""
    try:
        repo = Repo(dest)
        old = repo.head.commit.hexsha
        origin = repo.remotes.origin
        try:
            origin.fetch(prune=True)
        except Exception:
            try:
                repo.git.fetch("--unshallow", "origin")
            except Exception:
                origin.fetch(prune=True)
        if branch:
            try:
                repo.git.checkout("-B", branch, f"origin/{branch}")
            except Exception:
                repo.git.checkout(branch)
                repo.git.reset("--hard", f"origin/{branch}")
        else:
            try:
                active = repo.active_branch.name
            except TypeError:
                active = repo.git.symbolic_ref("refs/remotes/origin/HEAD").replace(
                    "refs/remotes/origin/", ""
                )
            repo.git.reset("--hard", f"origin/{active}")
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
    name = path.name.lower()
    if name.startswith("readme"):
        return "markdown"
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


def path_is_ignored(rel_path: str) -> bool:
    parts = set(rel_path.replace("\\", "/").split("/"))
    if parts & IGNORE_DIRS:
        return True
    name = rel_path.split("/")[-1].lower()
    return any(name.endswith(suf) for suf in IGNORE_SUFFIXES)


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
