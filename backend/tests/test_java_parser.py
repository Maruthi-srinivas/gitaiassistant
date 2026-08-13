import pytest

tree_sitter = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_java")

from app.parsers.java_parser import parse_java


def test_parse_java_symbols_imports_extends_calls():
    code = """
import java.util.List;
import com.example.Helper;

public class Greeter extends BaseGreeter implements Runnable {
    public Greeter() {
        Helper.init();
    }

    public void hello(String name) {
        Helper.say(name);
    }
}
"""
    result = parse_java(code)
    names = {s.name for s in result.symbols}
    assert "Greeter" in names
    assert "Greeter.hello" in names
    assert "Greeter.<init>" in names
    assert any(d.type == "IMPORTS" and "Helper" in d.target_name for d in result.dependencies)
    assert any(d.type == "EXTENDS" and d.target_name == "BaseGreeter" for d in result.dependencies)
    assert any(d.type == "IMPLEMENTS" and d.target_name == "Runnable" for d in result.dependencies)
    assert any(d.type == "CALLS" for d in result.dependencies)
