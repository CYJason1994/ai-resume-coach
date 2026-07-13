"""FastAPI 应用入口（M0 最小可跑闭环）。

- CORS：显式来源（生产禁止 *）。
- 中间件：请求 ID 绑定（日志关联）；token 不进日志。
- 路由：/health,/ready；/api/upload,/api/tasks,/api/jobs。
- 错误处理：类型化异常全局处理。
- 启动：init_db()（M0 dev 建表 + pgvector）。
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import init_db
from app.core.errors import register_error_handlers
from app.core.llm import get_llm
from app.core.logging import bind_request_id, get_logger, setup_logging
from app.core.quota import get_quota_enforcer, quota_subject
from app.routers import (
    auth,
    health,
    interview_sessions,
    interviews,
    jobs,
    result,
    resumes,
    tasks,
    upload,
)

settings = get_settings()
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await get_llm().aclose()


app = FastAPI(title=settings.APP_NAME, version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = bind_request_id()
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# ── 配额限流（M4 W2）：per-subject 固定窗口，叠加于 per-IP + LLM 信号量(6) ──
@app.middleware("http")
async def quota_middleware(request: Request, call_next):
    if request.url.path.startswith("/api"):
        token = request.headers.get("X-Access-Token") or request.cookies.get(
            settings.AUTH_COOKIE_NAME
        )
        subject = quota_subject(token, request.client.host if request.client else None)
        try:
            allowed = await get_quota_enforcer().allow(subject)
        except Exception:  # noqa: BLE001 — 兜底放行
            allowed = True
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "title": "QUOTA_EXCEEDED",
                    "status": 429,
                    "detail": "请求过于频繁，请稍后再试",
                    "request_id": getattr(request.state, "request_id", None),
                },
            )
    return await call_next(request)


register_error_handlers(app)
app.include_router(health.router)
app.include_router(upload.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(result.router, prefix="/api")
app.include_router(resumes.router, prefix="/api")
app.include_router(interviews.router)
app.include_router(interview_sessions.router)
app.include_router(auth.router, prefix="/api")


# ── 上传限流（M1-6）：基于客户端 IP 的内存令牌桶；生产改用 Redis ──
_upload_hits: dict[str, deque[float]] = defaultdict(deque)
UPLOAD_LIMIT = 10
UPLOAD_WINDOW = 60.0


@app.middleware("http")
async def rate_limit_upload(request: Request, call_next):
    if request.url.path == "/api/upload" and request.method == "POST":
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        dq = _upload_hits[ip]
        while dq and now - dq[0] > UPLOAD_WINDOW:
            dq.popleft()
        if len(dq) >= UPLOAD_LIMIT:
            return JSONResponse(
                status_code=429,
                content={
                    "title": "RATE_LIMITED",
                    "status": 429,
                    "detail": "上传过于频繁，请稍后再试",
                    "request_id": getattr(request.state, "request_id", None),
                },
            )
        dq.append(now)
    return await call_next(request)
