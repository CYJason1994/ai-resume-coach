"""Alembic 环境（M4 引入）。

- 连接串统一从 `app.core.config.get_settings().DATABASE_URL` 取，单一来源；
- 同步引擎用于迁移：若 DATABASE_URL 是 asyncpg（异步）驱动，自动规整为同步
  psycopg 驱动（Alembic 不支持异步引擎在线执行 DDL）；
- target_metadata = Base.metadata（导入 app.models.models 注册全部表）；
- 离线模式（`alembic upgrade head --sql`）可不连库生成 SQL，用于 CI/校验。
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.db import Base
from app.models import models  # noqa: F401  注册全部表到 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
db_url = settings.DATABASE_URL
# 异步驱动 → 同步驱动（Alembic 在线执行需同步引擎）
if db_url.startswith("postgresql+asyncpg://"):
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_name="postgresql",
        compare_type=True,
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": db_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
