from app.core.security import generate_access_token, hash_token, verify_token


def test_token_roundtrip():
    tok = generate_access_token()
    assert len(tok) >= 32
    h = hash_token(tok)
    assert verify_token(tok, h) is True
    assert verify_token("wrong", h) is False
    assert verify_token(None, h) is False
    assert verify_token(tok, None) is False


def test_hash_is_deterministic():
    tok = generate_access_token()
    assert hash_token(tok) == hash_token(tok)
