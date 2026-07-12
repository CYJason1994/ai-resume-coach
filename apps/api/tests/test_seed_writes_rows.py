"""M0.1 回归：岗位摄入经 ARQ 任务后写入 rows（验证 P0-2 修复 + seed 实际生效）。

需要可达的 PostgreSQL（DATABASE_URL，asyncpg 驱动）。无 DB 时自动 skip（CI 配置
services: postgres）。摄入使用 json(curated) provider，不依赖外部 LLM/网络：
即使 embedding 调用失败被捕获，Job 行仍会提交（仅缺向量）。
"""
from __future__ import annotations

import asyncio
import importlib.util

import pytest

# 在导入 app 前确保数据路径已就绪（conftest 已设置；此处再次防御性确认）
importlib.import_module("tests.conftest")  # noqa: F401

from app.core.db import SessionLocal
from app.models.models import Job
from app.services.job_source import JsonProvider
from sqlalchemy import func, select


@pytest.fixture(scope="module", autouse=True)
def _require_db():
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg 未安装，跳过 DB 集成测试")
    # 探测 DB 可达
    from app.core.config import get_settings

    settings = get_settings()
    try:
        import asyncpg

        async def _probe():
            conn = await asyncpg.connect(settings.DATABASE_URL)
            await conn.close()

        asyncio.run(_probe())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"数据库不可达，跳过 DB 集成测试: {e}")


def test_json_seed_writes_rows():
    """用 json(curated) provider 摄入，验证 jobs 表确有行写入。"""
    provider = JsonProvider()
    result = asyncio.run(provider.seed())
    assert result["ingested"] + result["skipped"] >= 1

    async def _count():
        async with SessionLocal() as s:
            return await s.scalar(select(func.count()).select_from(Job))

    total = asyncio.run(_count())
    assert total >= 1
