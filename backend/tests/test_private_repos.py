from pathlib import Path
from unittest.mock import MagicMock, patch

from app.crypto import encrypt_secret
from app.models import User
from app.services.file_discovery import clone_repository
from app.services.github_service import parse_github_url
from app.services.repository_service import create_repository


@patch("app.services.repository_service.fetch_repo_metadata")
def test_private_repo_rejected_without_token(mock_meta):
    mock_meta.return_value = {"private": True, "default_branch": "main"}
    db = MagicMock()
    try:
        create_repository(db, "https://github.com/acme/secret")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "GITHUB_TOKEN" in str(exc)
    db.add.assert_not_called()


@patch("app.services.repository_service.get_settings")
@patch("app.services.repository_service.fetch_repo_metadata")
def test_private_repo_allowed_with_token(mock_meta, mock_settings):
    mock_meta.return_value = {"private": True, "default_branch": "main"}
    mock_settings.return_value.github_token = "ghp_test"
    db = MagicMock()
    create_repository(db, "https://github.com/acme/secret")
    db.add.assert_called_once()
    db.commit.assert_called()


@patch("app.services.repository_service.fetch_repo_metadata")
def test_private_repo_decrypts_user_encrypted_token(mock_meta):
    mock_meta.return_value = {"private": True, "default_branch": "main"}
    db = MagicMock()
    owner = User(email="u@example.com", password_hash="x", github_token=encrypt_secret("ghp_userSecret999"))
    create_repository(db, "https://github.com/acme/secret", owner=owner)
    mock_meta.assert_called_once_with("acme", "secret", token="ghp_userSecret999")


@patch("app.services.file_discovery.Repo.clone_from")
def test_clone_repository_does_not_embed_token_in_url(mock_clone_from, tmp_path: Path):
    fake_repo = MagicMock()
    fake_repo.head.commit.hexsha = "abc1234"
    mock_clone_from.return_value = fake_repo

    parsed = parse_github_url("https://github.com/acme/secret")
    dest = tmp_path / "repo"
    secret_pat = "ghp_SensitiveTokenValue123"
    sha = clone_repository(parsed, dest, token=secret_pat)

    assert sha == "abc1234"
    clone_url_arg = mock_clone_from.call_args[0][0]
    kwargs = mock_clone_from.call_args[1]
    assert secret_pat not in clone_url_arg
    assert clone_url_arg == "https://github.com/acme/secret.git"
    assert "c" in kwargs
    assert any("http.https://github.com/.extraheader=Authorization: Basic " in c for c in kwargs["c"])
