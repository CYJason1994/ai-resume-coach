"""数据库访问：SQLAlchemy 异步引擎 + 会话 + Base。

M0：dev 用 `init_db()` 建表（含 pgvector 扩展）。
注意：生产迁移应使用 Alembic（M1 补），此处仅为 M0 最小闭环。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT.lower() == "development",
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """M0 dev-only：启用 pgvector 扩展并建表。

    生产环境改用 Alembic（M1）。pgvector 扩展用 pgvector/pgvector 镜像预装，
    此处幂等 CREATE EXTENSION IF NOT EXISTS。

    容错：DB 暂不可达时仅告警（liveness 仍可用，readiness 会报 degraded），
    便于本地/CI 无 DB 时启动；生产 M1 会接测试库与正式迁移。
    """
    from sqlalchemy import text

    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_jobs_embedding_hnsw "
                    "ON jobs USING hnsw (embedding vector_cosine_ops);"
                )
            )
    except Exception as e:  # noqa: BLE001
        print(f"[init_db] 跳过（数据库暂不可达）: {e}")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
