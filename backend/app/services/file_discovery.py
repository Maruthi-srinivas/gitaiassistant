from __future__ import annotations

import base64
import hashlib
import logging
import os
import shutil
from pathlib import Path

from git import Repo

from app.config import get_settings
from app.crypto import decrypt_secret, redact_secrets
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
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".go": "go",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
}


def repo_local_path(repo_id: str) -> Path:
    settings = get_settings()
    path = Path(settings.data_dir) / str(repo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _clone_url(parsed: ParsedRepoUrl, token: str | None = None) -> str:
    return parsed.clone_url


def _git_auth_config(token: str | None = None) -> list[str]:
    settings = get_settings()
    raw = decrypt_secret(token) if token else settings.github_token
    auth = (raw or "").strip()
    if not auth:
        return []
    b64_token = base64.b64encode(f"x-access-token:{auth}".encode("utf-8")).decode("ascii")
    return [f"http.https://github.com/.extraheader=Authorization: Basic {b64_token}"]


def _sanitize_remote_url(repo: Repo, clean_url: str | None = None) -> None:
    try:
        origin = repo.remotes.origin
        current_url = next(origin.urls, "")
        sanitized = clean_url or redact_secrets(current_url).replace("***@", "")
        if "@" in current_url and sanitized:
            origin.set_url(sanitized)
    except Exception:  # noqa: BLE001
        pass


def clone_repository(
    parsed: ParsedRepoUrl, dest: Path, branch: str | None = None, token: str | None = None
) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    url = _clone_url(parsed)
    auth_cfg = _git_auth_config(token)
    depth = max(1, int(settings.git_clone_depth or 200))
    logger.info("Cloning %s into %s (depth=%s, branch=%s)", parsed.html_url, dest, depth, branch)
    kwargs: dict = {"depth": depth, "no_single_branch": True}
    if branch:
        kwargs["branch"] = branch
    if auth_cfg:
        kwargs["c"] = auth_cfg
    try:
        repo = Repo.clone_from(url, dest, **kwargs)
    except Exception as exc:
        clean_msg = redact_secrets(str(exc), token, settings.github_token)
        raise RuntimeError(f"Git clone failed: {clean_msg}") from None
    _sanitize_remote_url(repo, parsed.clone_url)
    if branch:
        checkout_branch(dest, branch, token=token)
    return repo.head.commit.hexsha


def checkout_branch(dest: Path, branch: str, token: str | None = None) -> str:
    repo = Repo(dest)
    settings = get_settings()
    auth_cfg = _git_auth_config(token)
    c_args: list[str] = []
    for cfg in auth_cfg:
        c_args.extend(["-c", cfg])
    try:
        repo.git.execute(["git", *c_args, "fetch", "origin", branch, f"--depth={settings.git_clone_depth}"])
    except Exception:  # noqa: BLE001
        try:
            repo.git.execute(["git", *c_args, "fetch", "origin", branch])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not fetch branch %s: %s",
                branch,
                redact_secrets(str(exc), token, settings.github_token),
            )
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


def pull_or_fetch(dest: Path, branch: str | None = None, token: str | None = None) -> tuple[str, str]:
    """Return (old_commit, new_commit). Raises RuntimeError with a clear message on failure."""
    settings = get_settings()
    auth_cfg = _git_auth_config(token)
    c_args: list[str] = []
    for cfg in auth_cfg:
        c_args.extend(["-c", cfg])
    try:
        repo = Repo(dest)
        _sanitize_remote_url(repo)
        old = repo.head.commit.hexsha
        try:
            repo.git.execute(["git", *c_args, "fetch", "--prune", "origin"])
        except Exception:
            try:
                repo.git.execute(["git", *c_args, "fetch", "--unshallow", "origin"])
            except Exception:
                repo.git.execute(["git", *c_args, "fetch", "--prune", "origin"])
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
        clean_err = redact_secrets(str(exc), token, settings.github_token)
        raise RuntimeError(
            f"Incremental git fetch failed ({clean_err}). Use Analyze for a full re-index."
        ) from None


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


def load_file_content(file_rec, local_path: str | Path | None = None) -> str:
    """Read file content from the cloned git worktree, falling back to DB content if unavailable."""
    if file_rec is None:
        return ""
    rel_path = getattr(file_rec, "path", None)
    repo_id = getattr(file_rec, "repository_id", None)
    if rel_path and (local_path or repo_id):
        try:
            root = Path(local_path) if local_path else repo_local_path(str(repo_id))
            root_resolved = root.resolve()
            candidate = (root_resolved / rel_path).resolve()
            if candidate.is_relative_to(root_resolved) and candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    return getattr(file_rec, "content", None) or ""

