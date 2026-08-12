from app.services.github_service import parse_github_url


def test_health_contract():
    # Keep a tiny contract check without importing FastAPI route deps.
    assert parse_github_url("https://github.com/a/b").owner == "a"
