"""FastAPI 应用入口（M0 最小可跑闭环）。

- CORS：显式来源（生产禁止 *）。
- 中间件：请求 ID 绑定（日志关联）；token 不进日志。
- 路由：/health,/ready；/api/upload,/api/tasks,/api/jobs。
- 错误处理：类型化异常全局处理。
- 启动：init_db()（M0 dev 建表 + pgvector）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.core.errors import register_error_handlers
from app.core.llm import get_llm
from app.core.logging import bind_request_id, setup_logging
from app.routers import health, jobs, tasks, upload

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


register_error_handlers(app)
app.include_router(health.router)
app.include_router(upload.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
