import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.auth import (
    create_access_token,
    decode_access_token,
     hash_password,
    is_token_revoked,
    revoke_access_token,
    validate_password_strength,
    verify_password,
)
from app.config import Settings, validate_runtime_security
from app.crypto import decrypt_secret, encrypt_secret, redact_secrets


def test_password_hash_roundtrip():
    hashed = hash_password("secret-pass")
    assert hashed != "secret-pass"
    assert verify_password("secret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_password_strength_validation():
    validate_password_strength("StrongPass1")
    with pytest.raises(ValueError, match="at least 8"):
        validate_password_strength("Ab1")
    with pytest.raises(ValueError, match="uppercase"):
        validate_password_strength("lowercase123")
    with pytest.raises(ValueError, match="lowercase"):
        validate_password_strength("UPPERCASE123")
    with pytest.raises(ValueError, match="digit"):
        validate_password_strength("NoDigitsHere")


def test_access_token_roundtrip_and_jti():
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "a@b.com")
    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["email"] == "a@b.com"
    assert "jti" in payload and len(payload["jti"]) >= 32


@patch("app.auth.get_redis")
def test_token_revocation_in_redis(mock_get_redis):
    fake_redis = MagicMock()
    fake_redis.exists.return_value = 1
    mock_get_redis.return_value = fake_redis

    user_id = uuid.uuid4()
    token = create_access_token(user_id, "a@b.com")
    payload = decode_access_token(token)

    revoke_access_token(token)
    fake_redis.setex.assert_called_once()
    key, ttl, val = fake_redis.setex.call_args[0]
    assert key == f"revoked_jti:{payload['jti']}"
    assert ttl > 0
    assert val == "1"

    assert is_token_revoked(payload["jti"]) is True


def test_encrypt_decrypt_secret_roundtrip():
    raw = "ghp_superSecretToken1234567890"
    encrypted = encrypt_secret(raw)
    assert encrypted is not None
    assert encrypted.startswith("enc:v1:")
    assert raw not in encrypted
    assert decrypt_secret(encrypted) == raw
    # Idempotent when already encrypted
    assert encrypt_secret(encrypted) == encrypted
    # Legacy plaintext fallback
    assert decrypt_secret("legacy_token") == "legacy_token"
    assert encrypt_secret("   ") is None
    assert decrypt_secret(None) is None


def test_validate_runtime_security_production():
    dev_settings = Settings(environment="development")
    validate_runtime_security(dev_settings)

    weak_prod = Settings(environment="production", jwt_secret="short", encryption_key="some-key")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_runtime_security(weak_prod)

    no_enc_prod = Settings(
        environment="production",
        jwt_secret="a" * 40,
        encryption_key="",
    )
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        validate_runtime_security(no_enc_prod)

    valid_prod = Settings(
        environment="production",
        jwt_secret="a" * 40,
        encryption_key="b" * 32,
    )
    validate_runtime_security(valid_prod)


def test_redact_secrets_strips_tokens():
    pat = "ghp_1234567890abcdefABCDEF"
    custom = "my_custom_pat_value"
    msg = (
        f"fatal: Authentication failed for 'https://{pat}@github.com/acme/private.git/' "
        f"with header Authorization: Basic dGVzdA== and secret {custom}"
    )
    cleaned = redact_secrets(msg, custom)
    assert pat not in cleaned
    assert custom not in cleaned
    assert "dGVzdA==" not in cleaned
    assert "***@github.com" in cleaned
