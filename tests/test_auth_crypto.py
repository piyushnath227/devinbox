"""Session tokens (PyJWT) and password hashing (scrypt + legacy compatibility)."""

import hashlib
from datetime import datetime, timedelta, timezone

import jwt

from app.services.crypto_service import CryptoService

SECRET = "unit-test-secret-key-that-is-long-enough-32b"


def test_token_roundtrip():
    c = CryptoService(SECRET)
    payload = c.verify_token(c.create_access_token({"user": "admin"}))
    assert payload["user"] == "admin" and payload["type"] == "access_token"


def test_tampered_wrong_key_and_garbage_tokens_rejected():
    c = CryptoService(SECRET)
    t = c.create_access_token({"user": "admin"})
    assert c.verify_token(t[:-3] + "abc") is None
    assert CryptoService(SECRET + "x").verify_token(t) is None
    assert c.verify_token("not-a-jwt") is None


def test_expired_token_rejected():
    c = CryptoService(SECRET)
    old = jwt.encode({"user": "admin", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, SECRET, algorithm="HS256")
    assert c.verify_token(old) is None


def test_alg_none_token_rejected():
    c = CryptoService(SECRET)
    forged = jwt.encode({"user": "admin"}, key=None, algorithm="none")
    assert c.verify_token(forged) is None


def test_password_hash_is_scrypt_salted_and_verifies():
    c = CryptoService(SECRET)
    h1, h2 = c.hash_password("correct horse"), c.hash_password("correct horse")
    assert h1.startswith("scrypt$") and h1 != h2  # unique salt per hash
    assert c.verify_password("correct horse", h1)
    assert not c.verify_password("wrong", h1)


def test_legacy_sha256_hashes_still_verify():
    c = CryptoService(SECRET)
    salt = "ab" * 32
    legacy = f"{salt}:{hashlib.sha256((salt + 'oldpass').encode()).hexdigest()}"
    assert c.verify_password("oldpass", legacy) and not c.verify_password("nope", legacy)


def test_malformed_hashes_fail_closed():
    c = CryptoService(SECRET)
    for bad in ["", "garbage", "scrypt$1$2", "a:b:c", None]:
        assert c.verify_password("x", bad) is False
