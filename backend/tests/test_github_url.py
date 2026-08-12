import pytest

from app.services.github_service import parse_github_url


def test_parse_github_url_ok():
    parsed = parse_github_url("https://github.com/pallets/flask")
    assert parsed.owner == "pallets"
    assert parsed.name == "flask"
    assert parsed.clone_url.endswith("flask.git")


def test_parse_github_url_git_suffix():
    parsed = parse_github_url("https://github.com/pallets/flask.git/")
    assert parsed.owner == "pallets"
    assert parsed.name == "flask"


def test_parse_github_url_invalid():
    with pytest.raises(ValueError):
        parse_github_url("https://gitlab.com/foo/bar")
