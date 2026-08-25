from app.parsers.java_parser import parse_java
from app.parsers.javascript_parser import parse_javascript
from app.parsers.python_parser import parse_python
from app.parsers.typescript_parser import parse_typescript

try:
    from app.parsers.cpp_parser import parse_cpp
except Exception:  # pragma: no cover - optional grammar
    parse_cpp = None  # type: ignore

try:
    from app.parsers.go_parser import parse_go
except Exception:  # pragma: no cover - optional grammar
    parse_go = None  # type: ignore

__all__ = [
    "parse_python",
    "parse_javascript",
    "parse_typescript",
    "parse_java",
    "parse_cpp",
    "parse_go",
]
