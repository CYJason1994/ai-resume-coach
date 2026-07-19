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
from typing import Any, cast

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

    # M4 遗留3 收口：类型层面公开 **kwargs，使 logger.info("msg", key=value) 结构化字段
    # 通过 mypy 检查（运行时经上面覆写的 _log 注入 extra["struct"]，行为不变）。
    # 注意：个别历史调用用 msg= 关键字传递人类消息，与位置 msg（事件名）撞名；
    # 已在调用点统一改为 message=，此处 pop 一次作防御，避免重复绑定。
    def debug(self, msg: object, *args: object, exc_info: object = None,
              stack_info: bool = False, extra: object = None, **kwargs: object) -> None:
        kwargs.pop("msg", None)
        self._log(logging.DEBUG, msg, args, exc_info=exc_info, stack_info=stack_info, extra=extra, **kwargs)

    def info(self, msg: object, *args: object, exc_info: object = None,
             stack_info: bool = False, extra: object = None, **kwargs: object) -> None:
        kwargs.pop("msg", None)
        self._log(logging.INFO, msg, args, exc_info=exc_info, stack_info=stack_info, extra=extra, **kwargs)

    def warning(self, msg: object, *args: object, exc_info: object = None,
                stack_info: bool = False, extra: object = None, **kwargs: object) -> None:
        kwargs.pop("msg", None)
        self._log(logging.WARNING, msg, args, exc_info=exc_info, stack_info=stack_info, extra=extra, **kwargs)

    def error(self, msg: object, *args: object, exc_info: object = None,
              stack_info: bool = False, extra: object = None, **kwargs: object) -> None:
        kwargs.pop("msg", None)
        self._log(logging.ERROR, msg, args, exc_info=exc_info, stack_info=stack_info, extra=extra, **kwargs)

    def critical(self, msg: object, *args: object, exc_info: object = None,
                 stack_info: bool = False, extra: object = None, **kwargs: object) -> None:
        kwargs.pop("msg", None)
        self._log(logging.CRITICAL, msg, args, exc_info=exc_info, stack_info=stack_info, extra=extra, **kwargs)

    def exception(self, msg: object, *args: object, exc_info: object = True,
                  stack_info: bool = False, extra: object = None, **kwargs: object) -> None:
        kwargs.pop("msg", None)
        self._log(logging.ERROR, msg, args, exc_info=exc_info, stack_info=stack_info, extra=extra, **kwargs)


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
    return cast("StructuredLogger", logging.getLogger(name))
