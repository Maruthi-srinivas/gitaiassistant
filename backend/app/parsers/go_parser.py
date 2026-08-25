from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_go as tsgo

from app.parsers.base import ParseResult, ParsedDependency, ParsedSymbol, _line, _node_text
from app.parsers.edge_heuristics import event_edge_type

_GO_LANGUAGE = Language(tsgo.language())


def parse_go(content: str) -> ParseResult:
    parser = Parser(_GO_LANGUAGE)
    source = content.encode("utf-8")
    tree = parser.parse(source)
    result = ParseResult()
    package_name = "<module>"

    def walk(node, parent_type: str | None = None):
        nonlocal package_name
        if node.type == "package_clause":
            for child in node.children:
                if child.type == "package_identifier":
                    package_name = _node_text(source, child)
                    start, end = _line(node)
                    result.symbols.append(
                        ParsedSymbol(
                            name=package_name,
                            type="module",
                            start_line=start,
                            end_line=end,
                            signature=package_name,
                        )
                    )

        if node.type == "import_spec":
            path_node = node.child_by_field_name("path")
            raw = _node_text(source, path_node) if path_node else _node_text(source, node)
            target = raw.strip().strip('"')
            if target:
                result.dependencies.append(
                    ParsedDependency(source_name=package_name, target_name=target, type="IMPORTS")
                )

        if node.type == "type_spec":
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(name=name, type="class", start_line=start, end_line=end, signature=name)
                )
                if type_node and type_node.type == "struct_type":
                    _struct_embeds(type_node, name, result, source)
                for child in node.children:
                    walk(child, parent_type=name)
                return

        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(name=name, type="function", start_line=start, end_line=end, signature=name)
                )
                _extract_calls(node, name, result, source)
                if name.startswith("New"):
                    _extract_param_injections(node, name, result, source)

        if node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            recv = node.child_by_field_name("receiver")
            recv_name = _receiver_type(source, recv) if recv else parent_type
            if name_node:
                name = _node_text(source, name_node)
                full = f"{recv_name}.{name}" if recv_name else name
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(
                        name=full,
                        type="method",
                        start_line=start,
                        end_line=end,
                        parent_name=recv_name,
                        signature=full,
                    )
                )
                _extract_calls(node, full, result, source)

        for child in node.children:
            walk(child, parent_type=parent_type)

    walk(tree.root_node)
    return result


def _receiver_type(source: bytes, recv) -> str | None:
    text = _node_text(source, recv)
    cleaned = text.replace("*", " ").replace("(", " ").replace(")", " ").strip()
    parts = cleaned.split()
    return parts[-1] if parts else None


def _struct_embeds(struct_node, name: str, result: ParseResult, source: bytes) -> None:
    stack = list(struct_node.children)
    while stack:
        current = stack.pop()
        if current.type == "field_declaration":
            field_name = current.child_by_field_name("name")
            field_type = current.child_by_field_name("type")
            if field_type and not field_name:
                target = _node_text(source, field_type).replace("*", "").strip()
                if target:
                    result.dependencies.append(
                        ParsedDependency(source_name=name, target_name=target, type="EXTENDS")
                    )
        stack.extend(current.children)


def _extract_param_injections(node, source_name: str, result: ParseResult, source: bytes) -> None:
    params = node.child_by_field_name("parameters")
    if not params:
        return
    stack = list(params.children)
    while stack:
        current = stack.pop()
        if current.type == "parameter_declaration":
            type_node = current.child_by_field_name("type")
            if type_node:
                target = _node_text(source, type_node).replace("*", "").strip()
                if target and target[:1].isupper():
                    result.dependencies.append(
                        ParsedDependency(source_name=source_name, target_name=target, type="INJECTS")
                    )
        stack.extend(current.children)


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
