import pytest

tree_sitter = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

from app.parsers.python_parser import parse_python


def test_parse_python_symbols_and_calls():
    code = '''
class Greeter:
    def hello(self, name):
        return helper(name)

def helper(name):
    return f"hi {name}"
'''
    result = parse_python(code)
    names = {s.name for s in result.symbols}
    assert "Greeter" in names
    assert "Greeter.hello" in names
    assert "helper" in names
    assert any(d.type == "CALLS" for d in result.dependencies)
