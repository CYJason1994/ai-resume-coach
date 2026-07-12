"""匿名访问控制（R2-C2 完整契约）。

- 上传即签发 access_token = secrets.token_urlsafe(32)（~256bit），原值仅返回一次。
- DB 存 SHA-256 哈希（access_token_hash），即使库泄露也不暴露 token。
- 所有读路径须带 X-Access-Token（或 ?token=），服务端比对哈希，不匹配即 403。
- token 不进日志。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.config import get_settings

settings = get_settings()


def generate_access_token() -> str:
    return secrets.token_urlsafe(settings.ACCESS_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """计算 token 的存储哈希（与常量时间比对）。"""
    if settings.ACCESS_TOKEN_HASH_ALGO == "sha256":
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
    raise ValueError(f"不支持的哈希算法: {settings.ACCESS_TOKEN_HASH_ALGO}")


def verify_token(token: str | None, stored_hash: str | None) -> bool:
    if not token or not stored_hash:
        return False
    computed = hash_token(token)
    return hmac.compare_digest(computed, stored_hash)
