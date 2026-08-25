import pytest

tree_sitter = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_cpp")

from app.parsers.cpp_parser import parse_cpp


def test_parse_cpp_include_class_extends_calls():
    code = """
#include <vector>
#include "logger.h"

class Greeter : public BaseGreeter {
public:
    Greeter(Logger logger) {}
    void hello(const char* name) {
        helper(name);
        logger.publish(name);
    }
};

void helper(const char* name) {}
"""
    result = parse_cpp(code)
    names = {s.name for s in result.symbols}
    assert "Greeter" in names
    assert any("hello" in s.name for s in result.symbols)
    assert any(d.type == "IMPORTS" and "vector" in d.target_name for d in result.dependencies)
    assert any(d.type == "EXTENDS" and "BaseGreeter" in d.target_name for d in result.dependencies)
    assert any(d.type == "CALLS" for d in result.dependencies)
    assert any(d.type == "INJECTS" and "Logger" in d.target_name for d in result.dependencies)
    assert any(d.type == "PUBLISHES" for d in result.dependencies)
