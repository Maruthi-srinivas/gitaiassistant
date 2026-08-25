from unittest.mock import MagicMock, patch

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
