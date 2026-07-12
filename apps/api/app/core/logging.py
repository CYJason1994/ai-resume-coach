"""结构化 JSON 日志（请求 ID 传播，绝不记录 token/PII/密钥）。

M0 用标准 logging + JSON formatter；M1 升级 structlog + Sentry。

支持 logger.info("msg", key=value) 结构化字段（M0.1 修复：原 get_logger 返回裸
stdlib logger，不接受任意 kwargs，导致所有结构化日志调用在运行时抛
TypeError: Logger._log() got an unexpected keyword argument 'error'）。
通过自定义 StructuredLogger + setLoggerClass 集中解决，调用点无需改动。
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
        # 结构化字段（由 StructuredLogger 经 extra["struct"] 注入）
        kws = getattr(record, "struct", None)
        if isinstance(kws, dict):
            payload.update(kws)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class StructuredLogger(logging.Logger):
    """接受 logger.info("msg", key=value)，将 kwargs 作为结构化字段输出。"""

    def _log(self, level, msg, args, exc_info=None, extra=None, stack_info=False,
             stacklevel=1, **kwargs):
        struct = kwargs
        if struct:
            extra = dict(extra or {})
            extra["struct"] = struct
        super()._log(
            level, msg, args,
            exc_info=exc_info, extra=extra, stack_info=stack_info, stacklevel=stacklevel,
        )


# 在任何 getLogger 之前设置，确保后续创建的 logger 均为 StructuredLogger
logging.setLoggerClass(StructuredLogger)


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


def get_logger(name: str) -> StructuredLogger:
    return logging.getLogger(name)
