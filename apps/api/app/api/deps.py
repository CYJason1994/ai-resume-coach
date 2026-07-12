"""认证依赖：从请求提取 access_token（header 优先，其次 query）。

注意：本依赖只负责"取出 token"，**不**做 DB 校验（校验需在路由内按 resume 比对）。
token 绝不进入日志。
"""
from __future__ import annotations

from fastapi import Request

from app.core.errors import ForbiddenError


def extract_access_token(request: Request) -> str | None:
    hdr = request.headers.get("X-Access-Token")
    if hdr:
        return hdr
    return request.query_params.get("token")


def require_access_token(request: Request) -> str:
    token = extract_access_token(request)
    if not token:
        raise ForbiddenError("缺少访问令牌（X-Access-Token 或 ?token=）")
    return token
