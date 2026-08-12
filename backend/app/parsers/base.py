from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedSymbol:
    name: str
    type: str
    start_line: int
    end_line: int
    parent_name: str | None = None
    signature: str | None = None


@dataclass
class ParsedDependency:
    source_name: str
    target_name: str
    type: str


@dataclass
class ParseResult:
    symbols: list[ParsedSymbol] = field(default_factory=list)
    dependencies: list[ParsedDependency] = field(default_factory=list)


def _node_text(source: bytes, node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1
