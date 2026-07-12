"""ARQ Worker 入口（docker-compose worker 服务执行：`arq app.workers.worker.WorkerSettings`）。"""
from __future__ import annotations

from arq import WorkerSettings
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.tasks import process_resume_task

settings = get_settings()

redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)


class WorkerSettings:
    redis_settings = redis_settings
    functions = [process_resume_task]
    # 并发与重试（生产可调）
    max_jobs = 10
    job_timeout = 120
    keep_result = 3600
    retry_on_error = True
