from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_typescript as tstypescript

from app.parsers.javascript_parser import _walk_js_like

_TS_LANGUAGE = Language(tstypescript.language_typescript())
_TSX_LANGUAGE = Language(tstypescript.language_tsx())


def parse_typescript(content: str, tsx: bool = False):
    language = _TSX_LANGUAGE if tsx else _TS_LANGUAGE
    parser = Parser(language)
    source = content.encode("utf-8")
    tree = parser.parse(source)
    return _walk_js_like(tree.root_node, source)
