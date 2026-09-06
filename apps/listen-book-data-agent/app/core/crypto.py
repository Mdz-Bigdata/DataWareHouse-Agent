"""敏感配置（如 LLM API Key）的落库加密与脱敏展示。

加密用 Fernet 对称加密。主密钥优先取环境变量 LLM_KEY_MASTER_SECRET；
未配置时从 AUTH_SECRET_KEY 派生（开发环境零配置，生产应显式设置独立主密钥）。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.conf.app_config import app_config


def _fernet() -> Fernet:
    secret = app_config.llm.key_master_secret or app_config.auth.secret_key
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """解密失败（密钥变更、数据损坏）时抛出 ValueError，不返回残缺明文。"""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("无法解密已存储的密钥，请检查主密钥配置") from exc


def mask_secret(plaintext: str) -> str:
    """脱敏展示：保留前 3 后 4，其余用 * 代替；过短则全掩码。"""
    if len(plaintext) <= 8:
        return "****"
    return f"{plaintext[:3]}****{plaintext[-4:]}"
