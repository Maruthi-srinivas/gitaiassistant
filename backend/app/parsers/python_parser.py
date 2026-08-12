from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_python as tspython

from app.parsers.base import ParseResult, ParsedDependency, ParsedSymbol, _line, _node_text

_PY_LANGUAGE = Language(tspython.language())


def parse_python(content: str) -> ParseResult:
    parser = Parser(_PY_LANGUAGE)
    source = content.encode("utf-8")
    tree = parser.parse(source)
    root = tree.root_node
    result = ParseResult()

    def walk(node, parent_class: str | None = None):
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                result.symbols.append(
                    ParsedSymbol(name=name, type="class", start_line=start, end_line=end, signature=name)
                )
                super_node = node.child_by_field_name("superclasses")
                if super_node:
                    for child in super_node.children:
                        if child.type in {"identifier", "attribute"}:
                            target = _node_text(source, child)
                            result.dependencies.append(
                                ParsedDependency(source_name=name, target_name=target, type="EXTENDS")
                            )
                for child in node.children:
                    walk(child, parent_class=name)
                return

        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(source, name_node)
                start, end = _line(node)
                sym_type = "method" if parent_class else "function"
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

        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    target = _node_text(source, child)
                    result.dependencies.append(
                        ParsedDependency(source_name=parent_class or "<module>", target_name=target, type="IMPORTS")
                    )
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        target = _node_text(source, name_node)
                        result.dependencies.append(
                            ParsedDependency(
                                source_name=parent_class or "<module>", target_name=target, type="IMPORTS"
                            )
                        )

        if node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module = _node_text(source, module_node) if module_node else ""
            for child in node.children:
                if child.type in {"dotted_name", "aliased_import"}:
                    if child.type == "dotted_name" and module_node and child.id == module_node.id:
                        continue
                    name_node = child.child_by_field_name("name") if child.type == "aliased_import" else child
                    if name_node and name_node.type in {"dotted_name", "identifier"}:
                        leaf = _node_text(source, name_node)
                        target = f"{module}.{leaf}" if module else leaf
                        result.dependencies.append(
                            ParsedDependency(
                                source_name=parent_class or "<module>", target_name=target, type="IMPORTS"
                            )
                        )
            if module and not any(d.target_name.startswith(module) for d in result.dependencies[-5:]):
                result.dependencies.append(
                    ParsedDependency(source_name=parent_class or "<module>", target_name=module, type="IMPORTS")
                )

        for child in node.children:
            walk(child, parent_class=parent_class)

    walk(root)
    return result


def _extract_calls(node, source_name: str, result: ParseResult, source: bytes) -> None:
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == "call":
            fn = current.child_by_field_name("function")
            if fn:
                target = _node_text(source, fn)
                result.dependencies.append(
                    ParsedDependency(source_name=source_name, target_name=target, type="CALLS")
                )
        stack.extend(current.children)
