"""类型化错误层级 + 全局异常处理器（一致的错误响应格式）。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.logging import get_logger

logger = get_logger("errors")


class AppError(Exception):
    """所有业务错误的基类。"""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404

    def __init__(self, resource: str, id_: str):
        super().__init__(f"{resource} not found: {id_}", code="NOT_FOUND", status_code=404)


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    status_code = 403

    def __init__(self, message: str = "禁止访问：缺少有效的访问令牌"):
        super().__init__(message, code="FORBIDDEN", status_code=403)


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422


class StorageError(AppError):
    code = "STORAGE_ERROR"
    status_code = 500


class LlmUnavailableError(AppError):
    code = "LLM_UNAVAILABLE"
    status_code = 503


def _error_body(code: str, status: int, detail: str, request_id: str | None) -> dict:
    return {"title": code, "status": status, "detail": detail, "request_id": request_id}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.status_code, exc.message, _rid(request)),
        )

    @app.exception_handler(HTTPException)
    async def _handle_http(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                "HTTP_ERROR", exc.status_code, str(exc.detail), _rid(request)
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "VALIDATION_ERROR", 422, "输入校验失败", _rid(request)
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception):
        logger.error("unexpected_error", exc_info=exc, request_id=_rid(request))
        return JSONResponse(
            status_code=500,
            content=_error_body("INTERNAL_ERROR", 500, "服务器内部错误", _rid(request)),
        )


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)
