from __future__ import annotations

from pathlib import Path

from git import Repo

from app.services.file_discovery import discover_files, path_is_ignored
from app.services.markdown_chunking import chunk_markdown


def _make_repo(tmp: Path) -> Path:
    repo_dir = tmp / "sample"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")

    (repo_dir / "README.md").write_text("# Hello\n\nDocs here.\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    repo.index.add(["README.md", "app.py"])
    repo.index.commit("initial")

    (repo_dir / "app.py").write_text("def main():\n    return 2\n", encoding="utf-8")
    (repo_dir / "util.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    repo.index.add(["app.py", "util.py"])
    repo.index.commit("update app and add util")

    (repo_dir / "app.py").write_text("def main():\n    return 3\n", encoding="utf-8")
    repo.index.add(["app.py"])
    repo.index.commit("bump main again")
    return repo_dir


def test_path_is_ignored():
    assert path_is_ignored("node_modules/x.js") is True
    assert path_is_ignored("src/app.py") is False


def test_discover_includes_markdown(tmp_path: Path):
    repo_dir = _make_repo(tmp_path)
    files = discover_files(repo_dir)
    langs = {f["language"] for f in files}
    paths = {f["path"] for f in files}
    assert "markdown" in langs
    assert "README.md" in paths
    assert "python" in langs


def test_chunk_markdown_by_heading():
    content = "# Title\n\nIntro paragraph.\n\n## Section\n\nMore text.\n" * 20
    chunks = chunk_markdown(content, max_chars=200)
    assert len(chunks) >= 1
    assert all(start >= 1 and end >= start for start, end, _ in chunks)


def test_git_diff_paths(tmp_path: Path):
    repo_dir = _make_repo(tmp_path)
    repo = Repo(repo_dir)
    assert len(list(repo.iter_commits())) >= 3
    commits = list(repo.iter_commits())
    latest = commits[0]
    parent = commits[1]
    diffs = parent.diff(latest)
    paths = [(d.b_path or d.a_path) for d in diffs]
    assert "app.py" in paths
