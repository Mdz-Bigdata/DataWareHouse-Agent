"""敏感配置加密与脱敏的单元测试。"""

from __future__ import annotations

import pytest

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret


def test_encrypt_decrypt_roundtrip():
    ciphertext = encrypt_secret("sk-test-1234567890abcdef")
    assert ciphertext != "sk-test-1234567890abcdef"
    assert "sk-test" not in ciphertext
    assert decrypt_secret(ciphertext) == "sk-test-1234567890abcdef"


def test_same_plaintext_produces_different_ciphertext():
    # Fernet 每次加密带随机 IV，密文不应可比较
    assert encrypt_secret("same-key") != encrypt_secret("same-key")


def test_decrypt_rejects_garbage():
    with pytest.raises(ValueError, match="无法解密"):
        decrypt_secret("not-a-fernet-token")


def test_decrypt_rejects_tampered_ciphertext():
    ciphertext = encrypt_secret("sk-real-key")
    with pytest.raises(ValueError):
        decrypt_secret(ciphertext[:-4] + "AAAA")


def test_mask_secret():
    assert mask_secret("sk-1234567890abcdef") == "sk-****cdef"
    assert mask_secret("short") == "****"
    assert mask_secret("12345678") == "****"
