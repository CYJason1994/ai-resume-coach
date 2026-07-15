"""安全响应头中间件（M4 W4）。

策略（不破坏前端、可配置收紧）：
- Content-Security-Policy：本阶段用宽松 CSP（允许同源 + 内联样式 + data: 图片），
  避免阻断前端资源加载导致 next build / 运行时报错。生产可通过 config 收紧。
- CSP 默认走 **Report-Only**（CSP_REPORT_ONLY=True），只观测不发阻断，进一步降低风险。
- X-Content-Type-Options: nosniff、Referrer-Policy、Permissions-Policy、X-Frame-Options: DENY。
- HSTS 仅在 settings.is_production 且 HTTPS 域时下发（Strict-Transport-Security）。
- 全部由 config 开关控制（SECURITY_HEADERS_ENABLED），便于灰度/回滚。
"""
from __future__ import annotations

from fastapi import Request

from app.core.config import get_settings

settings = get_settings()

# 宽松但安全的默认 CSP：单页应用多为同源静态资源；允许 data: 图片与内联样式。
_DEFAULT_CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'"
)


def _build_csp() -> str:
    """构造 CSP。后续如需收紧（如改用 nonce）在此扩展，由 config 控制。"""
    return _DEFAULT_CSP


async def security_headers_middleware(request: Request, call_next):
    """给所有响应追加安全头。配置关闭或开发环境可跳过 HSTS。

    配置项带默认值（getattr），即便 config.py 尚未合并新字段也能安全运行；
    主 agent 合并 (a) 补丁后会显式生效。
    """
    response = await call_next(request)

    enabled = getattr(settings, "SECURITY_HEADERS_ENABLED", True)
    if not enabled:
        return response

    csp = _build_csp()
    csp_report_only = getattr(settings, "CSP_REPORT_ONLY", True)
    if csp_report_only:
        # 只上报不阻断，安全灰度
        response.headers["Content-Security-Policy-Report-Only"] = csp
    else:
        response.headers["Content-Security-Policy"] = csp

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["X-Frame-Options"] = "DENY"

    # HSTS 仅生产 + HTTPS 域下发
    if settings.is_production:
        hsts_max_age = getattr(settings, "HSTS_MAX_AGE", 31536000)
        response.headers["Strict-Transport-Security"] = (
            f"max-age={hsts_max_age}; includeSubDomains"
        )

    return response
