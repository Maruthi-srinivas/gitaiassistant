from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_cpp as tscpp

from app.parsers.base import ParseResult, ParsedDependency, ParsedSymbol, _line, _node_text
from app.parsers.edge_heuristics import event_edge_type

_CPP_LANGUAGE = Language(tscpp.language())
_PRIMITIVE = {"void", "int", "char", "bool", "float", "double", "long", "short", "unsigned", "auto"}


def parse_cpp(content: str) -> ParseResult:
    parser = Parser(_CPP_LANGUAGE)
    source = content.encode("utf-8")
    tree = parser.parse(source)
    result = ParseResult()

    def walk(node, parent_class: str | None = None):
        if node.type == "preproc_include":
            text = _node_text(source, node)
            target = text.replace("#include", "").strip().strip("\"<>")
            if target:
                result.dependencies.append(
                    ParsedDependency(
                        source_name=parent_class or "<module>",
                        target_name=target,
                        type="IMPORTS",
                    )
                )

        if node.type in {"class_specifier", "struct_specifier"}:
            name_node = node.child_by_field_name("name")
            name = _node_text(source, name_node) if name_node else None
            if not name:
                for child in node.children:
                    if child.type in {"type_identifier", "identifier"}:
                        name = _node_text(source, child)
                        break
            if name:
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(name=name, type="class", start_line=start, end_line=end, signature=name)
                )
                for child in node.children:
                    if child.type in {"base_class_clause", "base_clause"}:
                        for sub in child.children:
                            target = _base_name(source, sub)
                            if target:
                                result.dependencies.append(
                                    ParsedDependency(source_name=name, target_name=target, type="EXTENDS")
                                )
                    elif child.type == "base_class_specifier":
                        target = _base_name(source, child)
                        if target:
                            result.dependencies.append(
                                ParsedDependency(source_name=name, target_name=target, type="EXTENDS")
                            )
                for child in node.children:
                    walk(child, parent_class=name)
                return

        if node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            fname = _function_name(source, declarator) if declarator else None
            if fname:
                start, end = _line(node)
                short = fname.split("::")[-1]
                is_ctor = bool(parent_class and short == parent_class)
                if is_ctor:
                    full = f"{parent_class}.<init>"
                elif parent_class:
                    full = f"{parent_class}.{short}"
                else:
                    full = fname
                result.symbols.append(
                    ParsedSymbol(
                        name=full,
                        type="method" if parent_class else "function",
                        start_line=start,
                        end_line=end,
                        parent_name=parent_class,
                        signature=full,
                    )
                )
                _extract_calls(node, full, result, source)
                if is_ctor:
                    _extract_constructor_injections(node, parent_class or full, result, source)

        for child in node.children:
            walk(child, parent_class=parent_class)

    walk(tree.root_node)
    return result


def _base_name(source: bytes, node) -> str | None:
    text = _node_text(source, node)
    for token in ("public", "private", "protected", "virtual"):
        text = text.replace(token, " ")
    target = text.strip().split()[-1] if text.strip() else ""
    target = target.strip(":,{}")
    if not target or target in {":", ",", "{", "public", "private", "protected"}:
        return None
    return target


def _function_name(source: bytes, declarator) -> str | None:
    current = declarator
    for _ in range(12):
        if current is None:
            return None
        field = current.child_by_field_name("name")
        if field:
            return _node_text(source, field)
        for child in current.children:
            if child.type in {"identifier", "qualified_identifier", "field_identifier", "destructor_name"}:
                return _node_text(source, child)
        current = current.child_by_field_name("declarator")
    return None


def _extract_constructor_injections(node, source_name: str, result: ParseResult, source: bytes) -> None:
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "parameter_declaration":
            type_node = current.child_by_field_name("type")
            raw = _node_text(source, type_node) if type_node else ""
            target = raw.replace("const", " ").replace("&", " ").replace("*", " ").strip()
            target = target.split()[-1] if target else ""
            if target and target not in _PRIMITIVE:
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
