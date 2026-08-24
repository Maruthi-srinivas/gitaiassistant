"""Pure text helpers for chunk embedding / FTS (no DB deps)."""

from __future__ import annotations


def build_chunk_tsv(
    path: str,
    body: str,
    *,
    class_name: str | None = None,
    method_name: str | None = None,
    language: str | None = None,
) -> str:
    """Searchable text blob: path + symbols + body (used for FTS)."""
    parts = [path]
    if class_name:
        parts.append(class_name)
    if method_name:
        parts.append(method_name)
    if language:
        parts.append(language)
    parts.append(body)
    return "\n".join(parts)[:8000]


def build_embed_text(
    path: str,
    body: str,
    *,
    class_name: str | None = None,
    method_name: str | None = None,
    language: str | None = None,
) -> str:
    """Contextual prefix for embedding only; stored content stays clean code."""
    symbol = method_name or class_name or ""
    header_lines = [f"File: {path}"]
    if symbol:
        header_lines.append(f"Symbol: {symbol}")
    if language:
        header_lines.append(f"Language: {language}")
    header = "\n".join(header_lines)
    return f"{header}\n\n{body}"[:6000]
