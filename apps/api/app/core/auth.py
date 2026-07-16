"""账号鉴权核心（M4 W1）：argon2id 密码哈希 + JWT 会话令牌。

设计要点：
- 密码哈希用 argon2id（argon2-cffi 默认），抗 GPU/侧信道；
- 会话令牌为 HS256 JWT，写入 httpOnly SameSite cookie（见 routers/auth.py）；
- AUTH_JWT_SECRET 为空时退化为不安全 dev 默认并告警（生产必须设置，fail-loud）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger("auth")

# P0-1：生产环境若未配置 AUTH_JWT_SECRET，拒绝以公开默认密钥启动（fail-loud）。
# 否则攻击者可伪造任意用户会话 JWT → 账号接管。非生产保留不安全 dev 默认 + 告警。
if settings.is_production and not settings.AUTH_JWT_SECRET:
    raise RuntimeError(
        "AUTH_JWT_SECRET must be set in production "
        "(refusing to start with an insecure default)"
    )

_ph = PasswordHasher()  # argon2id 默认参数（内存/迭代随库版本演进）
_SECRET = settings.AUTH_JWT_SECRET or "dev-insecure-session-secret-CHANGE-ME"
if not settings.AUTH_JWT_SECRET:
    logger.warning(
        "auth_jwt_secret_missing",
        env=settings.ENVIRONMENT,
        message="AUTH_JWT_SECRET 未设置，使用不安全 dev 默认；生产必须设置该环境变量",
    )


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001 — argon2 其他异常一律视为校验失败
        return False


def create_session_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=settings.AUTH_JWT_TTL_SECONDS)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def decode_session_token(token: str) -> uuid.UUID | None:
    """有效返回 user_id，否则 None（过期/篡改/格式错）。"""
    try:
        data = jwt.decode(token, _SECRET, algorithms=["HS256"])
        return uuid.UUID(data["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
