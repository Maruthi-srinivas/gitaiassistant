from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava

from app.parsers.base import ParseResult, ParsedDependency, ParsedSymbol, _line, _node_text

_JAVA_LANGUAGE = Language(tsjava.language())


def parse_java(content: str) -> ParseResult:
    parser = Parser(_JAVA_LANGUAGE)
    source = content.encode("utf-8")
    tree = parser.parse(source)
    result = ParseResult()

    def type_name(node) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node:
            return _node_text(source, name_node)
        return None

    def walk(node, parent_class: str | None = None):
        if node.type in {"class_declaration", "interface_declaration", "enum_declaration"}:
            name = type_name(node)
            if name:
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(name=name, type="class", start_line=start, end_line=end, signature=name)
                )
                # superclass / interfaces
                for child in node.children:
                    if child.type == "superclass":
                        for sub in child.children:
                            if sub.type in {"type_identifier", "scoped_type_identifier", "generic_type"}:
                                target = _node_text(source, sub).split("<", 1)[0].strip()
                                if target:
                                    result.dependencies.append(
                                        ParsedDependency(source_name=name, target_name=target, type="EXTENDS")
                                    )
                    if child.type == "super_interfaces":
                        for sub in child.children:
                            if sub.type in {"type_list", "type_identifier", "scoped_type_identifier", "generic_type"}:
                                if sub.type == "type_list":
                                    for item in sub.children:
                                        if item.type in {
                                            "type_identifier",
                                            "scoped_type_identifier",
                                            "generic_type",
                                        }:
                                            target = _node_text(source, item).split("<", 1)[0].strip()
                                            if target:
                                                result.dependencies.append(
                                                    ParsedDependency(
                                                        source_name=name, target_name=target, type="IMPLEMENTS"
                                                    )
                                                )
                                else:
                                    target = _node_text(source, sub).split("<", 1)[0].strip()
                                    if target:
                                        result.dependencies.append(
                                            ParsedDependency(
                                                source_name=name, target_name=target, type="IMPLEMENTS"
                                            )
                                        )
                for child in node.children:
                    walk(child, parent_class=name)
                return

        if node.type == "method_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                full = f"{parent_class}.{name}" if parent_class else name
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

        if node.type == "constructor_declaration":
            name_node = node.child_by_field_name("name")
            ctor = _node_text(source, name_node) if name_node else (parent_class or "<init>")
            start, end = _line(node)
            full = f"{parent_class}.<init>" if parent_class else ctor
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

        if node.type == "import_declaration":
            text = _node_text(source, node).strip().rstrip(";")
            # import [static] com.example.Foo[. *]
            parts = text.split()
            if parts and parts[0] == "import":
                parts = parts[1:]
            if parts and parts[0] == "static":
                parts = parts[1:]
            if parts:
                target = parts[0]
                if target.endswith(".*"):
                    target = target[:-2]
                if target:
                    result.dependencies.append(
                        ParsedDependency(
                            source_name=parent_class or "<module>",
                            target_name=target,
                            type="IMPORTS",
                        )
                    )

        for child in node.children:
            walk(child, parent_class=parent_class)

    walk(tree.root_node)
    return result


def _extract_calls(node, source_name: str, result: ParseResult, source: bytes) -> None:
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "method_invocation":
            name_node = current.child_by_field_name("name")
            if name_node:
                target = _node_text(source, name_node)
            else:
                target = _node_text(source, current)
            # Prefer simple method name; keep object.method when present as object field
            obj = current.child_by_field_name("object")
            if obj and name_node:
                target = f"{_node_text(source, obj)}.{_node_text(source, name_node)}"
            if target:
                result.dependencies.append(
                    ParsedDependency(source_name=source_name, target_name=target, type="CALLS")
                )
        stack.extend(current.children)
