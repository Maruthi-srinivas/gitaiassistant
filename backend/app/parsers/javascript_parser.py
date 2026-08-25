from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_javascript as tsjavascript

from app.parsers.base import ParseResult, ParsedDependency, ParsedSymbol, _line, _node_text
from app.parsers.edge_heuristics import event_edge_type

_JS_LANGUAGE = Language(tsjavascript.language())


def parse_javascript(content: str) -> ParseResult:
    parser = Parser(_JS_LANGUAGE)
    source = content.encode("utf-8")
    tree = parser.parse(source)
    return _walk_js_like(tree.root_node, source)


def _walk_js_like(root, source: bytes) -> ParseResult:
    result = ParseResult()

    def walk(node, parent_class: str | None = None):
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(name=name, type="class", start_line=start, end_line=end, signature=name)
                )
                for child in node.children:
                    if child.type == "class_heritage":
                        for sub in child.children:
                            if sub.type == "identifier":
                                result.dependencies.append(
                                    ParsedDependency(
                                        source_name=name, target_name=_node_text(source, sub), type="EXTENDS"
                                    )
                                )
                for child in node.children:
                    walk(child, parent_class=name)
                return

        if node.type in {"function_declaration", "method_definition", "generator_function_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                sym_type = "method" if parent_class or node.type == "method_definition" else "function"
                full = f"{parent_class}.{name}" if parent_class else name
                result.symbols.append(
                    ParsedSymbol(
                        name=full,
                        type=sym_type,
                        start_line=start,
                        end_line=end,
                        parent_name=parent_class,
                        signature=full,
                    )
                )
                _extract_calls(node, full, result, source)
                if name == "constructor" and parent_class:
                    _extract_js_constructor_injections(node, parent_class, result, source)

        if node.type == "lexical_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    name_node = child.child_by_field_name("name")
                    value = child.child_by_field_name("value")
                    if name_node and value and value.type in {"arrow_function", "function"}:
                        name = _node_text(source, name_node)
                        start, end = _line(child)
                        full = f"{parent_class}.{name}" if parent_class else name
                        result.symbols.append(
                            ParsedSymbol(
                                name=full,
                                type="function",
                                start_line=start,
                                end_line=end,
                                parent_name=parent_class,
                                signature=full,
                            )
                        )
                        _extract_calls(value, full, result, source)

        if node.type in {"import_statement", "export_statement"}:
            text = _node_text(source, node)
            for part in text.replace(";", " ").split():
                if part.startswith(".") or "/" in part or part.startswith("'") or part.startswith('"'):
                    target = part.strip("'\"")
                    if target and target not in {"from", "import", "export", "default", "as", "*"}:
                        result.dependencies.append(
                            ParsedDependency(
                                source_name=parent_class or "<module>", target_name=target, type="IMPORTS"
                            )
                        )

        for child in node.children:
            walk(child, parent_class=parent_class)

    walk(root)
    return result


def _extract_js_constructor_injections(node, source_name: str, result: ParseResult, source: bytes) -> None:
    params = node.child_by_field_name("parameters")
    if not params:
        return
    for child in params.children:
        if child.type == "identifier":
            target = _node_text(source, child)
            if target:
                result.dependencies.append(
                    ParsedDependency(source_name=source_name, target_name=target, type="INJECTS")
                )


def _extract_calls(node, source_name: str, result: ParseResult, source: bytes) -> None:
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            fn = current.child_by_field_name("function")
            if fn:
                target = _node_text(source, fn)
                result.dependencies.append(
                    ParsedDependency(source_name=source_name, target_name=target, type="CALLS")
                )
                event = event_edge_type(target)
                if event:
                    result.dependencies.append(
                        ParsedDependency(source_name=source_name, target_name=target, type=event)
                    )
        stack.extend(current.children)
