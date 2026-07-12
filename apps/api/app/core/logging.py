"""结构化 JSON 日志（请求 ID 传播，绝不记录 token/PII/密钥）。

M0 用标准 logging + JSON formatter；M1 升级 structlog + Sentry。
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from typing import Any

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "logger": record.name,
            "request_id": request_id_ctx.get(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def bind_request_id() -> str:
    rid = uuid.uuid4().hex[:16]
    request_id_ctx.set(rid)
    return rid


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
