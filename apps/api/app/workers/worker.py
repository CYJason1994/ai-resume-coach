"""ARQ Worker 入口。

docker-compose worker 服务执行：`python -m app.workers.worker`。

兼容 arq 0.28+ 新 API：通过 Worker(functions=..., redis_settings=..., on_startup=...)
构造并 .run()（旧版 `from arq import WorkerSettings` 在此版本已移除，会 ImportError）。
"""
from __future__ import annotations

from arq.connections import RedisSettings
from arq.worker import Worker

from app.core.config import get_settings
from app.workers.tasks import process_resume_task, seed_jobs_task

settings = get_settings()
redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)


async def on_startup(ctx) -> None:
    """worker 进程不跑 FastAPI lifespan，此处确保建表（含 pgvector 扩展）。"""
    from app.core.db import init_db

    await init_db()


def build_worker() -> Worker:
    return Worker(
        functions=[process_resume_task, seed_jobs_task],
        redis_settings=redis_settings,
        on_startup=on_startup,
        # 并发与重试（生产可调）
        max_jobs=10,
        job_timeout=120,
        keep_result=3600,
    )


if __name__ == "__main__":
    build_worker().run()
