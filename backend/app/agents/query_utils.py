from __future__ import annotations

import re

CITATION_RE = re.compile(
    r"(?P<file>(?:commit:[0-9a-fA-F]+)|(?:[\w./\\-]+?\.[A-Za-z0-9]+)):(?P<start>\d+)-(?P<end>\d+)"
    r"|(?P<commit>commit:[0-9a-fA-F]{7,40})",
    re.IGNORECASE,
)


def classify_query(question: str) -> list[str]:
    """Return one or more retrieval paths. Always includes RAG."""
    q = question.lower()
    kinds = ["RAG"]
    if any(
        k in q
        for k in (
            "churn",
            "why was",
            "why did",
            "changed",
            "commit",
            "history",
            "between",
            "diff",
            "co-change",
            "together",
            "recent change",
            "affected by",
            "highest churn",
        )
    ):
        kinds.append("HISTORY")
    if any(
        k in q
        for k in ("readme", "documentation", "docs", "how do i", "getting started", "install")
    ):
        kinds.append("DOCS")
    if any(k in q for k in ("depend", "call", "import", "extends", "who uses", "graph", "path")):
        kinds.append("GRAPH")
    if any(
        k in q
        for k in ("where is", "defined", "definition", "symbol", "function", "class", "method")
    ):
        kinds.append("CODE")
    seen: set[str] = set()
    out: list[str] = []
    for k in kinds:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def citations_match(answer: str, chunks: list[dict]) -> bool:
    allowed_files = {c.get("file", "") for c in chunks if c.get("file")}
    allowed_basenames = {f.split("/")[-1] for f in allowed_files}
    for m in CITATION_RE.finditer(answer or ""):
        commit = m.group("commit")
        if commit:
            if any(
                str(f).startswith("commit:") or str(f).startswith("_git") for f in allowed_files
            ):
                return True
            continue
        file_part = m.group("file") or ""
        if file_part.startswith("commit:"):
            return True
        if file_part in allowed_files or file_part.split("/")[-1] in allowed_basenames:
            return True
        for af in allowed_files:
            if file_part in af or af.endswith(file_part):
                return True
    return False


def ensure_citations_footer(answer: str, chunks: list[dict]) -> str:
    if citations_match(answer, chunks):
        return answer
    if any(
        str(c.get("file", "")).startswith("commit:") or str(c.get("file", "")).startswith("_git")
        for c in chunks
    ):
        if re.search(r"commit:[0-9a-fA-F]{7,40}", answer) or CITATION_RE.search(answer):
            return answer
    if chunks:
        footer = "\n\nSources:\n" + "\n".join(
            f"- {c['file']}:{c.get('start_line', 1)}-{c.get('end_line', 1)}"
            for c in chunks[:5]
            if c.get("file")
        )
        return answer + footer
    return answer
