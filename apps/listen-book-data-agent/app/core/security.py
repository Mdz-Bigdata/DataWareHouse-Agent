"""口令哈希与校验：PBKDF2-HMAC-SHA256，仅使用标准库，不保存明文。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from app.conf.app_config import app_config
from app.core.log import logger

PBKDF2_ITERATIONS = 120_000
_SCHEME = "pbkdf2_sha256"
_SALT_BYTES = 16
_DKLEN = 32


def hash_password(password: str) -> str:
    """返回 `pbkdf2_sha256$iterations$salt_hex$hash_hex` 格式的可校验串。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=_DKLEN
    )
    return f"{_SCHEME}${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """常数时间比较；任何格式异常都视为校验失败。"""
    try:
        scheme, iterations, salt_hex, hash_hex = stored_hash.split("$")
        if scheme != _SCHEME:
            return False
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


# ==================== Bearer 令牌（JWT HS256，带签名与有效期） ====================

_TOKEN_ALGORITHM = "HS256"


def create_access_token(*, user_id: str, username: str, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=app_config.auth.token_ttl_minutes),
    }
    return jwt.encode(payload, app_config.auth.secret_key, algorithm=_TOKEN_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """验签并检查有效期；任何失败（过期、篡改、格式错）统一返回 None。"""
    try:
        return jwt.decode(
            token, app_config.auth.secret_key, algorithms=[_TOKEN_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None


# ==================== 启动期密钥强度校验 ====================
#
# PyJWT 在 HS256 下对 < 32 字节的密钥会抛 InsecureKeyLengthWarning，
# 但仅是运行期告警、不影响签发。这里把它升级为启动期强校验：
# 弱密钥在非开发环境直接拒绝启动，避免生产误用默认值或短密钥。

# RFC 7518 §3.2 推荐 HS256 最小密钥长度（256 bit = 32 byte）。
_MIN_SECRET_BYTES = 32
# conf/app_config.yaml 中 AUTH_SECRET_KEY 的硬编码默认值，视为已知弱密钥。
_WEAK_DEFAULT_SECRETS = frozenset(
    {
        "dev-only-secret-change-me",
    }
)
# 视为开发环境的 environment 取值（大小写不敏感）。
_DEV_ENVIRONMENTS = frozenset({"development", "dev", "test", "testing", "local"})


def validate_secret_key(secret_key: str, environment: str) -> None:
    """启动期校验 JWT 签名密钥强度。

    判定规则（命中任一即为弱密钥）：
      1. 命中硬编码默认值（如 yaml 中的 `dev-only-secret-change-me`）；
      2. UTF-8 字节数 < 32（不符合 RFC 7518 §3.2 对 HS256 的推荐）。

    分级响应：
      - 开发环境（environment ∈ {_DEV_ENVIRONMENTS}）：logger.warning 后放行；
      - 非开发环境：raise RuntimeError 拒绝启动。
    """
    issues: list[str] = []
    if secret_key in _WEAK_DEFAULT_SECRETS:
        issues.append("使用了硬编码默认值，存在被猜测/伪造 JWT 的风险")
    key_bytes = len(secret_key.encode("utf-8"))
    if key_bytes < _MIN_SECRET_BYTES:
        issues.append(
            f"长度 {key_bytes} 字节 < {_MIN_SECRET_BYTES} 字节"
            "（RFC 7518 §3.2 推荐 HS256 最小密钥长度）"
        )
    if not issues:
        return

    message = "AUTH_SECRET_KEY 校验失败：" + "；".join(issues)
    if environment.lower() in _DEV_ENVIRONMENTS:
        logger.warning("{}（当前 environment={}，开发环境放行）", message, environment)
        return
    # 非开发环境：fail-fast，禁止用弱密钥签发生产 JWT。
    raise RuntimeError(message)
