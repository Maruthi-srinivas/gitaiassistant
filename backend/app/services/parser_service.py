from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.models import Dependency, FileRecord, Symbol
from app.parsers import parse_java, parse_javascript, parse_python, parse_typescript
from app.parsers.base import ParseResult

logger = logging.getLogger(__name__)


def parse_file_content(language: str, path: str, content: str) -> ParseResult:
    try:
        if language == "python":
            return parse_python(content)
        if language == "javascript":
            return parse_javascript(content)
        if language == "typescript":
            return parse_typescript(content, tsx=path.endswith(".tsx"))
        if language == "java":
            return parse_java(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parse failed for %s: %s", path, exc)
    return ParseResult()


def persist_parse_results(
    db: Session,
    repository_id: uuid.UUID,
    file_rec: FileRecord,
    parsed: ParseResult,
) -> dict[str, uuid.UUID]:
    name_to_id: dict[str, uuid.UUID] = {}
    for sym in parsed.symbols:
        parent_id = name_to_id.get(sym.parent_name) if sym.parent_name else None
        row = Symbol(
            file_id=file_rec.id,
            name=sym.name,
            type=sym.type,
            start_line=sym.start_line,
            end_line=sym.end_line,
            parent_symbol_id=parent_id,
            signature=sym.signature,
        )
        db.add(row)
        db.flush()
        name_to_id[sym.name] = row.id
        short = sym.name.split(".")[-1]
        name_to_id.setdefault(short, row.id)

    for dep in parsed.dependencies:
        db.add(
            Dependency(
                repository_id=repository_id,
                source_symbol_id=name_to_id.get(dep.source_name),
                target_symbol_id=name_to_id.get(dep.target_name) or name_to_id.get(dep.target_name.split(".")[-1]),
                source_name=dep.source_name,
                target_name=dep.target_name,
                type=dep.type,
                file_id=file_rec.id,
            )
        )
    return name_to_id
