import pytest

tree_sitter = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")

from app.parsers.javascript_parser import parse_javascript
from app.parsers.typescript_parser import parse_typescript
from app.services.parser_service import clip_name


def test_parse_javascript_symbols_and_calls():
    code = """
function helper(name) {
  return name;
}

function hello(name) {
  return helper(name);
}
"""
    result = parse_javascript(code)
    names = {s.name for s in result.symbols}
    assert "helper" in names
    assert "hello" in names
    assert any(d.type == "CALLS" and d.target_name == "helper" for d in result.dependencies)


def test_chained_call_does_not_store_callback_body():
    """Regression: ponytail-style .filter(() => { ... }).join overflowed varchar(512)."""
    code = """
function filterSkillBodyForMode(body, mode) {
  const withoutFrontmatter = body.replace(/^---[\\s\\S]*?---/, "");
  return withoutFrontmatter
    .split(/\\r?\\n/)
    .filter((line) => {
      const tableLabel = line.match(/^\\|\\s*\\*\\*(.+?)\\*\\*\\s*\\|/);
      if (tableLabel) {
        const labelMode = normalizeMode(exampleLabel[1].trim());
        if (labelMode) return labelMode === effectiveMode;
      }
      return true;
    })
    .join("\\n");
}

function normalizeMode(value) {
  return value;
}
"""
    result = parse_javascript(code)
    call_targets = [d.target_name for d in result.dependencies if d.type == "CALLS"]
    assert call_targets, "expected CALLS edges"
    assert all(len(t) <= 512 for t in call_targets)
    assert all("\n" not in t for t in call_targets)
    assert all("tableLabel" not in t for t in call_targets)
    assert any(t.endswith("join") or t == "join" or t.endswith(".join") for t in call_targets)
    assert any("normalizeMode" in t or t == "normalizeMode" for t in call_targets)


def test_typescript_member_call_stays_compact():
    code = """
export function getPonytailInstructions() {
  return fs.readFileSync(path, "utf8");
}
"""
    result = parse_typescript(code)
    call_targets = [d.target_name for d in result.dependencies if d.type == "CALLS"]
    assert any("readFileSync" in t for t in call_targets)
    assert all(len(t) <= 512 and "\n" not in t for t in call_targets)


def test_clip_name_fits_varchar_512():
    huge = "withoutFrontmatter\n    .split\n    .filter((line) => { " + ("x" * 600) + " }).join"
    clipped = clip_name(huge)
    assert len(clipped) <= 512
    assert "\n" not in clipped
