from app.auth import create_access_token, hash_password, verify_password


def test_password_hash_roundtrip():
    hashed = hash_password("secret-pass")
    assert hashed != "secret-pass"
    assert verify_password("secret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip():
    import uuid

    user_id = uuid.uuid4()
    token = create_access_token(user_id, "a@b.com")
    from app.auth import decode_access_token

    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["email"] == "a@b.com"
