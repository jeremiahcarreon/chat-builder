import pytest
from cryptography.exceptions import InvalidTag

from app.crypto import decrypt, encrypt
from app.security import LoginThrottle, hash_password, verify_password


def test_encrypt_round_trip():
    token = encrypt("sk-ant-secret", "provider_key:anthropic")
    assert "sk-ant-secret" not in token
    assert decrypt(token, "provider_key:anthropic") == "sk-ant-secret"


def test_encrypt_is_randomized():
    assert encrypt("same", "ctx") != encrypt("same", "ctx")


def test_decrypt_rejects_wrong_context():
    token = encrypt("sk-ant-secret", "provider_key:anthropic")
    with pytest.raises(InvalidTag):
        decrypt(token, "provider_key:openai")


def test_password_hashing():
    hashed = hash_password("correct horse battery")
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong")


def test_login_throttle_blocks_after_limit():
    throttle = LoginThrottle(max_failures=3, window_seconds=60)
    for _ in range(3):
        assert not throttle.is_blocked("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert throttle.is_blocked("1.2.3.4")
    assert not throttle.is_blocked("5.6.7.8")
    throttle.reset("1.2.3.4")
    assert not throttle.is_blocked("1.2.3.4")
