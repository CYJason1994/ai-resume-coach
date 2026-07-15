"""安全审计（M4 W4）：结构化审计日志。

设计原则（合规 + 不破坏现有流）：
- 审计事件只走结构化日志（logger.info，event="audit"），本阶段**不引入新表/迁移**，
  降低范围风险；后续如需落地 AuditLog 表再补 Alembic 迁移。
- **绝不记录 PII / token 原值**：
  * subject 只接收「已脱敏的类型前缀」字符串（如 "user:ab12"、"anon:token:ab12"）。
  * 若调用方误传原始值（无类型前缀的长随机串），自动哈希为 "subj:<hash8>"，绝不落明文。
  * detail 中的敏感 key（token/password/secret/email/phone/...）一律改写为 "***REDACTED***"。
- 提供 FastAPI 依赖 get_audit_context(request)，方便路由层拿 request_id / subject 调 audit_event。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger("audit")

# subject 已知类型前缀：出现这些前缀且后缀不长时，视为已脱敏，原样保留。
_KNOWN_SUBJECT_PREFIXES = {"user", "anon", "ip", "svc", "system", "job", "session"}

# detail 中需强制脱敏的敏感 key（大小写不敏感）。
_SENSITIVE_KEYS = {
    "token", "access_token", "refresh_token", "password", "passwd", "secret",
    "authorization", "auth", "cookie", "api_key", "apikey", "session", "ssn",
    "id_card", "idcard", "phone", "mobile", "email", "mail",
    "ip", "client_ip", "remote_addr", "user_agent",
}


@dataclass
class AuditContext:
    """路由层通过 Depends(get_audit_context) 拿到的审计上下文。"""

    request_id: str | None
    subject: str


def anonymized_subject(token: str | None) -> str:
    """由匿名 access_token 推导审计主体（只留 sha256 前 8 位，绝不留原值）。"""
    if not token:
        return "anon:none"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    return f"anon:token:{digest}"


def user_subject(user_id: Any) -> str:
    """由已认证 user_id 推导审计主体（只留前 8 位十六进制前缀）。"""
    return f"user:{str(user_id)[:8]}"


def _redact_subject(subject: str | None) -> str | None:
    """保证 subject 不含原始敏感值：有类型前缀且后缀短则保留，否则哈希前缀。"""
    if subject is None:
        return None
    s = str(subject)
    if ":" in s:
        prefix, _, suffix = s.partition(":")
        if prefix in _KNOWN_SUBJECT_PREFIXES and len(suffix) <= 24:
            return s
    # 兜底：原始值（可能含 token/PII）→ 哈希前缀，绝不记录明文
    return f"subj:{hashlib.sha256(s.encode('utf-8')).hexdigest()[:8]}"


def _redact_detail(detail: Any) -> Any:
    """递归脱敏 detail 中的敏感 key。"""
    if isinstance(detail, dict):
        out: dict[str, Any] = {}
        for k, v in detail.items():
            if isinstance(k, str) and k.lower().strip() in _SENSITIVE_KEYS:
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact_detail(v)
        return out
    if isinstance(detail, (list, tuple)):
        return [_redact_detail(x) for x in detail]
    return detail


def audit_event(
    action: str,
    *,
    subject: str | None = None,
    resource: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
    request_id: str | None = None,
    db=None,  # 预留：未来若落地 AuditLog 表可用，本阶段不引入表/迁移
) -> None:
    """写一条结构化审计事件到日志（event="audit"）。

    绝不以任何形式记录 PII / token 原值——subject 与 detail 均经脱敏。
    """
    logger.info(
        f"audit:{action}",
        event="audit",
        action=action,
        subject=_redact_subject(subject),
        resource=resource,
        resource_id=resource_id,
        detail=_redact_detail(detail),
        request_id=request_id,
    )


def get_audit_context(request: Request = Depends()) -> AuditContext:
    """FastAPI 依赖：从请求推导 request_id / subject，供路由层调用 audit_event。

    主体优先级：会话 cookie（已认证）→ X-Access-Token（匿名哈希前缀）→ 未识别。
    全程不接触 token 原值明文。
    """
    rid = getattr(request.state, "request_id", None)
    token = request.headers.get("X-Access-Token") or request.cookies.get(
        settings.AUTH_COOKIE_NAME
    )
    subject = anonymized_subject(token) if token else "anon:none"
    return AuditContext(request_id=rid, subject=subject)
