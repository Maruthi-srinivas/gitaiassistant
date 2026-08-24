from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import git as gitmod
from git import Repo
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Commit, CommitFile, FileChurn, FileCoChange, RepositoryBranch
from app.services.file_discovery import list_remote_branches, path_is_ignored

logger = logging.getLogger(__name__)


def clear_history(db: Session, repository_id: uuid.UUID) -> None:
    commit_ids = [
        c.id for c in db.query(Commit.id).filter(Commit.repository_id == repository_id).all()
    ]
    if commit_ids:
        db.query(CommitFile).filter(CommitFile.commit_id.in_(commit_ids)).delete(
            synchronize_session=False
        )
    db.query(Commit).filter(Commit.repository_id == repository_id).delete(synchronize_session=False)
    db.query(FileChurn).filter(FileChurn.repository_id == repository_id).delete(
        synchronize_session=False
    )
    db.query(FileCoChange).filter(FileCoChange.repository_id == repository_id).delete(
        synchronize_session=False
    )
    db.flush()


def sync_branches(
    db: Session,
    repository_id: uuid.UUID,
    dest: Path,
    indexed_branch: str | None,
    indexed_sha: str | None,
) -> list[RepositoryBranch]:
    remote = list_remote_branches(dest)
    existing = {
        b.name: b
        for b in db.query(RepositoryBranch)
        .filter(RepositoryBranch.repository_id == repository_id)
        .all()
    }
    seen: set[str] = set()
    for item in remote:
        name = item["name"]
        seen.add(name)
        row = existing.get(name)
        if not row:
            row = RepositoryBranch(repository_id=repository_id, name=name)
            db.add(row)
            existing[name] = row
        row.commit_hash = item.get("commit_hash")
        row.is_indexed = bool(indexed_branch and name == indexed_branch)
        if row.is_indexed and indexed_sha:
            row.commit_hash = indexed_sha
    for name, row in list(existing.items()):
        if name not in seen:
            db.delete(row)
    db.flush()
    return list(existing.values())


def _root_commit_paths(commit) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        for blob in commit.tree.traverse():
            if getattr(blob, "type", None) == "blob":
                rel = blob.path.replace("\\", "/")
                if not path_is_ignored(rel):
                    out.append((rel, "A"))
    except Exception:  # noqa: BLE001
        pass
    return out


def extract_git_history(
    db: Session,
    repository_id: uuid.UUID,
    dest: Path,
    *,
    incremental: bool = False,
) -> dict:
    """Persist commits, commit_files, churn and co-change pairs. Returns metrics."""
    settings = get_settings()
    max_commits = max(1, int(settings.git_history_max_commits or 200))
    repo = Repo(dest)

    if not incremental:
        clear_history(db, repository_id)
        known_shas: set[str] = set()
    else:
        known_shas = {
            c.sha
            for c in db.query(Commit.sha).filter(Commit.repository_id == repository_id).all()
        }

    commits_iter = list(repo.iter_commits(max_count=max_commits))
    commits_iter.reverse()

    inserted = 0
    co_counter: Counter[tuple[str, str]] = Counter()
    churn_counter: Counter[str] = Counter()
    last_sha: dict[str, str] = {}

    if incremental:
        for row in db.query(FileChurn).filter(FileChurn.repository_id == repository_id).all():
            churn_counter[row.path] = row.change_count
            if row.last_commit_sha:
                last_sha[row.path] = row.last_commit_sha
        for row in db.query(FileCoChange).filter(FileCoChange.repository_id == repository_id).all():
            co_counter[(row.path_a, row.path_b)] = row.count

    for commit in commits_iter:
        sha = commit.hexsha
        if sha in known_shas:
            continue
        authored_at = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
        try:
            author = f"{commit.author.name} <{commit.author.email}>"
        except Exception:  # noqa: BLE001
            author = str(getattr(commit, "author", "") or "")
        row = Commit(
            repository_id=repository_id,
            sha=sha,
            author=author[:512] if author else None,
            authored_at=authored_at,
            message=(commit.message or "").strip()[:4000],
        )
        db.add(row)
        db.flush()

        paths: list[str] = []
        try:
            if commit.parents:
                diffs = commit.parents[0].diff(commit)
            else:
                diffs = commit.diff(gitmod.NULL_TREE)
        except Exception:  # noqa: BLE001
            diffs = []

        if not diffs and not commit.parents:
            for path, ctype in _root_commit_paths(commit):
                paths.append(path)
                db.add(CommitFile(commit_id=row.id, path=path, change_type=ctype))
                churn_counter[path] += 1
                last_sha[path] = sha
        else:
            for d in diffs:
                path = (d.b_path or d.a_path or "").replace("\\", "/")
                if not path or path_is_ignored(path):
                    continue
                if d.new_file:
                    ctype = "A"
                elif d.deleted_file:
                    ctype = "D"
                else:
                    ctype = "M"
                paths.append(path)
                db.add(CommitFile(commit_id=row.id, path=path, change_type=ctype))
                churn_counter[path] += 1
                last_sha[path] = sha

        uniq = sorted(set(paths))
        for a, b in combinations(uniq, 2):
            pair = (a, b) if a < b else (b, a)
            co_counter[pair] += 1

        inserted += 1
        known_shas.add(sha)

    if not incremental:
        db.query(FileChurn).filter(FileChurn.repository_id == repository_id).delete(
            synchronize_session=False
        )
        db.query(FileCoChange).filter(FileCoChange.repository_id == repository_id).delete(
            synchronize_session=False
        )
        db.flush()

    for path, count in churn_counter.items():
        existing = (
            db.query(FileChurn)
            .filter(FileChurn.repository_id == repository_id, FileChurn.path == path)
            .first()
        )
        if existing:
            existing.change_count = count
            existing.last_commit_sha = last_sha.get(path)
        else:
            db.add(
                FileChurn(
                    repository_id=repository_id,
                    path=path,
                    change_count=count,
                    last_commit_sha=last_sha.get(path),
                )
            )

    top_pairs = co_counter.most_common(500)
    if not incremental:
        for (a, b), count in top_pairs:
            db.add(FileCoChange(repository_id=repository_id, path_a=a, path_b=b, count=count))
    else:
        for (a, b), count in top_pairs:
            existing = (
                db.query(FileCoChange)
                .filter(
                    FileCoChange.repository_id == repository_id,
                    FileCoChange.path_a == a,
                    FileCoChange.path_b == b,
                )
                .first()
            )
            if existing:
                existing.count = count
            else:
                db.add(
                    FileCoChange(
                        repository_id=repository_id, path_a=a, path_b=b, count=count
                    )
                )

    db.flush()
    total = (
        db.query(func.count(Commit.id)).filter(Commit.repository_id == repository_id).scalar() or 0
    )
    logger.info("Git history for repo %s: inserted=%s total=%s", repository_id, inserted, total)
    return {"commits_inserted": inserted, "commits_total": int(total)}


def commits_for_path(
    db: Session, repository_id: uuid.UUID, path: str, limit: int = 20
) -> list[dict]:
    q = (
        db.query(Commit, CommitFile)
        .join(CommitFile, CommitFile.commit_id == Commit.id)
        .filter(Commit.repository_id == repository_id)
    )
    if path:
        q = q.filter(CommitFile.path.ilike(f"%{path}%"))
    rows = q.order_by(Commit.authored_at.desc()).limit(limit).all()
    out: list[dict] = []
    seen: set[str] = set()
    for commit, cf in rows:
        if commit.sha in seen:
            continue
        seen.add(commit.sha)
        out.append(
            {
                "sha": commit.sha,
                "author": commit.author,
                "authored_at": commit.authored_at.isoformat() if commit.authored_at else None,
                "message": commit.message,
                "path": cf.path,
                "change_type": cf.change_type,
            }
        )
    return out


def module_churn(db: Session, repository_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = (
        db.query(FileChurn)
        .filter(FileChurn.repository_id == repository_id)
        .order_by(FileChurn.change_count.desc())
        .limit(500)
        .all()
    )
    modules: Counter[str] = Counter()
    for r in rows:
        parts = r.path.split("/")
        module = parts[0] if len(parts) == 1 else "/".join(parts[:2])
        modules[module] += r.change_count
    return [{"module": name, "change_count": count} for name, count in modules.most_common(limit)]


def file_churn_top(db: Session, repository_id: uuid.UUID, limit: int = 30) -> list[dict]:
    rows = (
        db.query(FileChurn)
        .filter(FileChurn.repository_id == repository_id)
        .order_by(FileChurn.change_count.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "path": r.path,
            "change_count": r.change_count,
            "last_commit_sha": r.last_commit_sha,
        }
        for r in rows
    ]


def co_changing_files(
    db: Session, repository_id: uuid.UUID, path: str | None = None, limit: int = 20
) -> list[dict]:
    q = db.query(FileCoChange).filter(FileCoChange.repository_id == repository_id)
    if path:
        q = q.filter(
            (FileCoChange.path_a.ilike(f"%{path}%")) | (FileCoChange.path_b.ilike(f"%{path}%"))
        )
    rows = q.order_by(FileCoChange.count.desc()).limit(limit).all()
    return [{"path_a": r.path_a, "path_b": r.path_b, "count": r.count} for r in rows]


def compare_commits(
    db: Session,
    repository_id: uuid.UUID,
    dest: Path | None,
    from_sha: str,
    to_sha: str,
) -> dict:
    files: list[dict] = []
    if dest and dest.exists():
        try:
            repo = Repo(dest)
            diffs = repo.commit(from_sha).diff(to_sha)
            for d in diffs:
                path = (d.b_path or d.a_path or "").replace("\\", "/")
                if not path:
                    continue
                if d.new_file:
                    ctype = "A"
                elif d.deleted_file:
                    ctype = "D"
                else:
                    ctype = "M"
                files.append({"path": path, "change_type": ctype})
        except Exception as exc:  # noqa: BLE001
            logger.warning("git compare failed: %s", exc)

    if not files:
        to_row = (
            db.query(Commit)
            .filter(Commit.repository_id == repository_id, Commit.sha.ilike(f"{to_sha}%"))
            .first()
        )
        if to_row:
            cfiles = db.query(CommitFile).filter(CommitFile.commit_id == to_row.id).all()
            files = [{"path": f.path, "change_type": f.change_type} for f in cfiles]

    from_row = (
        db.query(Commit)
        .filter(Commit.repository_id == repository_id, Commit.sha.ilike(f"{from_sha}%"))
        .first()
    )
    to_row = (
        db.query(Commit)
        .filter(Commit.repository_id == repository_id, Commit.sha.ilike(f"{to_sha}%"))
        .first()
    )
    return {
        "from": {
            "sha": from_sha,
            "message": from_row.message if from_row else None,
            "authored_at": from_row.authored_at.isoformat()
            if from_row and from_row.authored_at
            else None,
        },
        "to": {
            "sha": to_sha,
            "message": to_row.message if to_row else None,
            "authored_at": to_row.authored_at.isoformat()
            if to_row and to_row.authored_at
            else None,
        },
        "files": files,
        "file_count": len(files),
    }


def recent_impact(db: Session, repository_id: uuid.UUID, limit: int = 15) -> dict:
    from app.models import Dependency, FileRecord, Symbol

    latest = (
        db.query(Commit)
        .filter(Commit.repository_id == repository_id)
        .order_by(Commit.authored_at.desc())
        .first()
    )
    if not latest:
        return {"commit": None, "files": [], "dependents": []}
    files = db.query(CommitFile).filter(CommitFile.commit_id == latest.id).limit(limit).all()
    paths = [f.path for f in files]
    file_rows = (
        db.query(FileRecord)
        .filter(FileRecord.repository_id == repository_id, FileRecord.path.in_(paths[:50]))
        .all()
        if paths
        else []
    )
    file_ids = [f.id for f in file_rows]
    symbols = (
        db.query(Symbol).filter(Symbol.file_id.in_(file_ids)).limit(40).all() if file_ids else []
    )
    names = [s.name for s in symbols]
    dependents = []
    if names:
        deps = (
            db.query(Dependency)
            .filter(
                Dependency.repository_id == repository_id,
                Dependency.target_name.in_(names),
            )
            .limit(40)
            .all()
        )
        dependents = [
            {"source": d.source_name, "target": d.target_name, "type": d.type} for d in deps
        ]
    return {
        "commit": {
            "sha": latest.sha,
            "message": latest.message,
            "author": latest.author,
            "authored_at": latest.authored_at.isoformat() if latest.authored_at else None,
        },
        "files": [{"path": f.path, "change_type": f.change_type} for f in files],
        "dependents": dependents,
    }


def get_git_history_context(
    db: Session,
    repository_id: uuid.UUID,
    question: str,
    dest: Path | None = None,
) -> list[dict]:
    """Build agent-facing context chunks for HISTORY questions."""
    q = question.lower()
    chunks: list[dict] = []

    if any(k in q for k in ("churn", "highest", "most changed", "change most")):
        top = module_churn(db, repository_id, limit=15)
        files = file_churn_top(db, repository_id, limit=15)
        content = "Module churn:\n" + "\n".join(
            f"- {m['module']}: {m['change_count']} changes" for m in top
        )
        content += "\n\nFile churn:\n" + "\n".join(
            f"- {f['path']}: {f['change_count']} (last={f.get('last_commit_sha')})" for f in files
        )
        chunks.append(
            {
                "file": "_git_churn",
                "start_line": 1,
                "end_line": 1,
                "content": content,
                "score": 1.5,
            }
        )

    if any(k in q for k in ("together", "co-change", "cochange", "change with")):
        pairs = co_changing_files(db, repository_id, limit=20)
        content = "Files that change together:\n" + "\n".join(
            f"- {p['path_a']} <-> {p['path_b']}: {p['count']}" for p in pairs
        )
        chunks.append(
            {
                "file": "_git_cochange",
                "start_line": 1,
                "end_line": 1,
                "content": content or "No co-change data.",
                "score": 1.4,
            }
        )

    if any(k in q for k in ("between", "compare", "diff", "what changed")):
        shas = re.findall(r"\b([0-9a-f]{7,40})\b", question, flags=re.I)
        if len(shas) >= 2:
            cmp = compare_commits(db, repository_id, dest, shas[0], shas[1])
            content = (
                f"Compare {cmp['from']['sha'][:8]} -> {cmp['to']['sha'][:8]}\n"
                f"From: {cmp['from'].get('message')}\nTo: {cmp['to'].get('message')}\n"
                f"Files ({cmp['file_count']}):\n"
                + "\n".join(f"- [{f['change_type']}] {f['path']}" for f in cmp["files"][:40])
            )
            chunks.append(
                {
                    "file": f"commit:{cmp['to']['sha'][:12]}",
                    "start_line": 1,
                    "end_line": 1,
                    "content": content,
                    "score": 1.6,
                }
            )

    if any(k in q for k in ("recent", "latest", "impact", "affected", "break")):
        impact = recent_impact(db, repository_id)
        if impact.get("commit"):
            c = impact["commit"]
            content = (
                f"Latest commit {c['sha'][:12]} by {c.get('author')}\n"
                f"{c.get('message')}\nFiles:\n"
                + "\n".join(
                    f"- [{f['change_type']}] {f['path']}" for f in impact.get("files") or []
                )
                + "\nDependents:\n"
                + "\n".join(
                    f"- {d['source']} -> {d['target']} ({d['type']})"
                    for d in impact.get("dependents") or []
                )
            )
            chunks.append(
                {
                    "file": f"commit:{c['sha'][:12]}",
                    "start_line": 1,
                    "end_line": 1,
                    "content": content,
                    "score": 1.5,
                }
            )

    path_hint = None
    m = re.search(r"([\w./-]+\.(?:py|js|ts|tsx|jsx|java|md))", question)
    if m:
        path_hint = m.group(1)
    else:
        m2 = re.search(r"`([^`]+)`", question)
        if m2:
            path_hint = m2.group(1)
        else:
            m3 = re.search(r"\b([A-Z][A-Za-z0-9_]{2,})\b", question)
            if m3:
                path_hint = m3.group(1)

    if path_hint or any(k in q for k in ("why", "changed", "history", "commit")):
        commits = commits_for_path(db, repository_id, path_hint or "", limit=15)
        if commits:
            content = f"Commits touching '{path_hint}':\n" + "\n".join(
                f"- {c['sha'][:10]} [{c.get('change_type')}] {c.get('authored_at')}: {c.get('message')}"
                for c in commits
            )
            chunks.append(
                {
                    "file": f"commit:{commits[0]['sha'][:12]}",
                    "start_line": 1,
                    "end_line": 1,
                    "content": content,
                    "score": 1.4,
                }
            )

    if not chunks:
        recent = (
            db.query(Commit)
            .filter(Commit.repository_id == repository_id)
            .order_by(Commit.authored_at.desc())
            .limit(10)
            .all()
        )
        content = "Recent commits:\n" + "\n".join(
            f"- {c.sha[:10]} {c.authored_at}: {(c.message or '')[:120]}" for c in recent
        )
        chunks.append(
            {
                "file": "_git_recent",
                "start_line": 1,
                "end_line": 1,
                "content": content or "No git history indexed yet.",
                "score": 1.0,
            }
        )
    return chunks
