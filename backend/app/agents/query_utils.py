from __future__ import annotations

import json
import logging
import re

from app.config import get_settings

logger = logging.getLogger(__name__)

CITATION_RE = re.compile(
    r"(?P<file>(?:commit:[0-9a-fA-F]+)|(?:[\w./\\-]+?\.[A-Za-z0-9]+)):(?P<start>\d+)-(?P<end>\d+)"
    r"|(?P<commit>commit:[0-9a-fA-F]{7,40})",
    re.IGNORECASE,
)

# English / discourse words — never treat as code symbols
QUERY_STOPWORDS = frozenset(
    {
        "where",
        "what",
        "when",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "how",
        "does",
        "did",
        "doing",
        "done",
        "the",
        "this",
        "that",
        "these",
        "those",
        "there",
        "their",
        "them",
        "then",
        "than",
        "from",
        "with",
        "into",
        "onto",
        "about",
        "after",
        "before",
        "between",
        "under",
        "over",
        "above",
        "below",
        "have",
        "has",
        "had",
        "having",
        "been",
        "being",
        "were",
        "was",
        "are",
        "is",
        "am",
        "will",
        "would",
        "could",
        "should",
        "can",
        "may",
        "might",
        "must",
        "shall",
        "need",
        "want",
        "please",
        "show",
        "find",
        "tell",
        "explain",
        "describe",
        "defined",
        "definition",
        "and",
        "or",
        "not",
        "for",
        "in",
        "on",
        "at",
        "to",
        "of",
        "a",
        "an",
        "it",
        "its",
        "you",
        "your",
        "me",
        "my",
        "we",
        "our",
        "they",
        "also",
        "just",
        "only",
        "any",
        "all",
        "some",
        "such",
        "same",
        "other",
        "another",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "much",
        "many",
        "own",
        "used",
        "using",
        "use",
        "uses",
        "here",
        "look",
        "looking",
        "see",
        "get",
        "got",
        "give",
        "given",
        "make",
        "made",
        "repo",
        "repository",
        "project",
        "source",
        "sources",
    }
)

# Extra words that are domain vocabulary but not identifier names
SYMBOL_STOPWORDS = QUERY_STOPWORDS | frozenset(
    {
        "function",
        "functions",
        "class",
        "classes",
        "method",
        "methods",
        "module",
        "modules",
        "file",
        "files",
        "code",
        "application",
        "applications",
        "factory",
        "factories",
        "call",
        "calls",
        "called",
        "calling",
        "main",
    }
)

# Backward-compatible alias
STOPWORDS = SYMBOL_STOPWORDS

_IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
_CAMEL_OR_SNAKE_RE = re.compile(
    r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|[a-z_][a-z0-9]*(?:_[a-z0-9]+)+)\b"
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


def extract_symbol_hints(question: str) -> list[str]:
    """Prefer backtick names and CamelCase/snake_case over English stopwords."""
    hints: list[str] = []
    for m in re.finditer(r"`([^`]+)`", question):
        name = m.group(1).strip()
        if name and name.lower() not in SYMBOL_STOPWORDS:
            hints.append(name)

    for m in _CAMEL_OR_SNAKE_RE.finditer(question):
        name = m.group(1)
        if name.lower() not in SYMBOL_STOPWORDS and name not in hints:
            hints.append(name)

    for m in _IDENTIFIER_RE.finditer(question):
        name = m.group(1)
        if name.lower() in SYMBOL_STOPWORDS:
            continue
        if name not in hints:
            hints.append(name)

    return hints[:5]


def extract_symbol_hint(question: str) -> str | None:
    hints = extract_symbol_hints(question)
    return hints[0] if hints else None


def _fallback_rewrite(question: str, history: str = "") -> dict:
    """Regex/stopword rewrite when LLM is unavailable."""
    rewrites = [question.strip()]
    symbols = extract_symbol_hints(question)
    if history:
        symbols.extend(extract_symbol_hints(history))
    # Dedupe symbols preserving order
    seen: set[str] = set()
    uniq_symbols: list[str] = []
    for s in symbols:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            uniq_symbols.append(s)

    # Build alternate queries from meaningful tokens + symbols
    # Use QUERY_STOPWORDS so domain words like "application factory" remain searchable
    tokens = [
        t
        for t in re.findall(r"[A-Za-z0-9_]+", question)
        if t.lower() not in QUERY_STOPWORDS and len(t) > 2
    ]
    if uniq_symbols:
        rewrites.append(" ".join(uniq_symbols[:3]))
    if tokens:
        alt = " ".join(tokens[:6])
        if alt.lower() not in {r.lower() for r in rewrites}:
            rewrites.append(alt)
    # Cap at 3
    rewrites = rewrites[:3]
    return {"rewrites": rewrites, "symbols": uniq_symbols[:5]}


def rewrite_query(question: str, history: str = "") -> dict:
    """Return {rewrites: [...], symbols: [...]} for multi-query retrieval."""
    settings = get_settings()
    fallback = _fallback_rewrite(question, history)
    if not settings.llm_api_key:
        return fallback

    hist_block = f"\nRecent conversation:\n{history}\n" if history.strip() else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You rewrite software repository questions for code search. "
                "Return JSON with keys rewrites (array of 2-3 short search queries) "
                "and symbols (array of likely code identifiers like function/class names). "
                "No markdown."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}{hist_block}",
        },
    ]
    try:
        from app.rag.embeddings import chat_completion

        raw = chat_completion(messages, temperature=0.0)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        rewrites = data.get("rewrites") or []
        symbols = data.get("symbols") or []
        if not isinstance(rewrites, list):
            rewrites = []
        if not isinstance(symbols, list):
            symbols = []
        rewrites = [str(r).strip() for r in rewrites if str(r).strip()][:3]
        symbols = [str(s).strip() for s in symbols if str(s).strip()][:5]
        if question.strip() and question.strip() not in rewrites:
            rewrites = [question.strip(), *rewrites][:3]
        if not rewrites:
            return fallback
        # Merge fallback symbols if LLM missed them
        for s in fallback.get("symbols") or []:
            if s not in symbols:
                symbols.append(s)
        return {"rewrites": rewrites, "symbols": symbols[:5]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("rewrite_query LLM failed: %s", exc)
        return fallback


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


def _strip_sources_footer(answer: str) -> str:
    """Remove a trailing Sources: dump so footer-only answers are not 'grounded'."""
    if not answer:
        return ""
    parts = re.split(r"\n\s*Sources:\s*\n", answer, maxsplit=1, flags=re.IGNORECASE)
    return parts[0].strip()


def is_grounded(answer: str, chunks: list[dict]) -> bool:
    """True only if the answer cites an allowed source (footer alone is not enough)."""
    if not (answer or "").strip():
        return False
    # Explicit insufficiency is acceptable without citation
    lower = answer.lower()
    if any(
        p in lower
        for p in (
            "not in the indexed sources",
            "could not find",
            "could not verify",
            "insufficient",
            "not found in the provided",
            "no relevant",
            "candidates (not confirmed",
        )
    ):
        return True
    body = _strip_sources_footer(answer)
    return citations_match(body, chunks)


def ungrounded_refusal(chunks: list[dict]) -> str:
    """Refuse with candidate files — not treated as proof."""
    lines = [
        "I could not verify that answer from the indexed sources.",
        "The following files were retrieved as candidates (not confirmed evidence):",
    ]
    for c in chunks[:5]:
        if c.get("file"):
            lines.append(
                f"- {c['file']}:{c.get('start_line', 1)}-{c.get('end_line', 1)}"
            )
    if len(lines) == 2:
        lines.append("- (no candidates)")
    return "\n".join(lines)


def ensure_citations_footer(answer: str, chunks: list[dict]) -> str:
    """Legacy helper: append Sources footer. Prefer ungrounded_refusal for chat."""
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


def format_history(messages: list[dict], max_turns: int = 6) -> str:
    """Format recent chat turns for prompts (role/content dicts)."""
    recent = messages[-max_turns:] if messages else []
    parts = []
    for m in recent:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content[:800]}")
    return "\n".join(parts)
