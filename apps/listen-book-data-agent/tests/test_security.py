"""PBKDF2 口令哈希单元测试。"""

from __future__ import annotations

from app.core.security import PBKDF2_ITERATIONS, hash_password, verify_password


def test_hash_format_contains_scheme_iterations_salt():
    stored = hash_password("admin123")
    scheme, iterations, salt_hex, hash_hex = stored.split("$")
    assert scheme == "pbkdf2_sha256"
    assert int(iterations) == PBKDF2_ITERATIONS
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(hash_hex)) == 32


def test_verify_correct_password():
    stored = hash_password("正确的口令")
    assert verify_password("正确的口令", stored) is True


def test_verify_wrong_password():
    stored = hash_password("admin123")
    assert verify_password("admin124", stored) is False


def test_same_password_produces_different_salts():
    assert hash_password("admin123") != hash_password("admin123")


def test_verify_rejects_malformed_hashes():
    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "md5$1$aa$bb") is False
    assert verify_password("x", "pbkdf2_sha256$abc$zz$yy") is False


def test_plaintext_is_not_stored():
    stored = hash_password("admin123")
    assert "admin123" not in stored
