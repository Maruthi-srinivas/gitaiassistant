import pytest

tree_sitter = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_go")

from app.parsers.go_parser import parse_go


def test_parse_go_package_struct_import_call():
    code = """
package greeter

import "fmt"

type Handler struct {
    Logger
}

func NewHandler(logger Logger) *Handler {
    return &Handler{}
}

func (h *Handler) Hello(name string) {
    fmt.Println(name)
    h.Logger.publish(name)
}
"""
    result = parse_go(code)
    names = {s.name for s in result.symbols}
    assert "greeter" in names
    assert "Handler" in names
    assert "NewHandler" in names
    assert any("Hello" in s.name for s in result.symbols)
    assert any(d.type == "IMPORTS" and "fmt" in d.target_name for d in result.dependencies)
    assert any(d.type == "EXTENDS" and d.target_name == "Logger" for d in result.dependencies)
    assert any(d.type == "CALLS" for d in result.dependencies)
    assert any(d.type == "INJECTS" and "Logger" in d.target_name for d in result.dependencies)
    assert any(d.type == "PUBLISHES" for d in result.dependencies)
