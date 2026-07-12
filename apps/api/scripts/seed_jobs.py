"""岗位源摄入 CLI（R2-C1 可配置）。

用法（在 apps/api 目录，确保 PYTHONPATH=. 且 DATABASE_URL 可达）：
    python scripts/seed_jobs.py

读取 settings.JOB_SOURCE（onet|json）→ 调用对应 Provider.seed()。
O*NET 快照缺失时 OnetProvider 自动回退 curated；运行 data/jobs/onet/ingest.py 生成快照。
"""
from __future__ import annotations

import asyncio

from app.services.job_source import get_job_source


async def main() -> None:
    provider = get_job_source()
    result = await provider.seed()
    print("seed result:", result)


if __name__ == "__main__":
    asyncio.run(main())
