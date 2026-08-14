from __future__ import annotations

import re


def chunk_markdown(content: str, max_chars: int = 1000) -> list[tuple[int, int, str]]:
    """Split markdown by headings into chunks of ~max_chars. Returns (start_line, end_line, body)."""
    lines = content.splitlines()
    if not lines:
        return []
    sections: list[tuple[int, list[str]]] = []
    current_start = 1
    current: list[str] = []
    for i, line in enumerate(lines, start=1):
        if re.match(r"^#{1,6}\s+", line) and current:
            sections.append((current_start, current))
            current_start = i
            current = [line]
        else:
            if not current:
                current_start = i
            current.append(line)
    if current:
        sections.append((current_start, current))

    chunks: list[tuple[int, int, str]] = []
    for start, sect_lines in sections:
        buf: list[str] = []
        buf_start = start
        for offset, line in enumerate(sect_lines):
            buf.append(line)
            body = "\n".join(buf)
            if len(body) >= max_chars:
                end = buf_start + len(buf) - 1
                chunks.append((buf_start, end, body))
                buf = []
                buf_start = start + offset + 1
        if buf:
            end = buf_start + len(buf) - 1
            body = "\n".join(buf)
            if body.strip():
                chunks.append((buf_start, end, body))
    if not chunks and content.strip():
        chunks.append((1, len(lines), content[: max_chars * 2]))
    return chunks
