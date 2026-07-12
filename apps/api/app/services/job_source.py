"""岗位源 Provider（R2-C1 可配置）。

默认 `onet`：从 O*NET 30.3（CC BY 4.0, 美国劳工部）摄入职业 → 映射为 jobs 行；
应用可配置中文标题/技能 overlay（zh_overlay.json，自有 IP）。
备选 `json`：加载手写/覆盖层 curated JSON（自有 IP）。
摄入脚本 scripts/seed_jobs.py 调用本 Provider。

署名要求见 data/jobs/README.md。
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.llm import get_llm
from app.core.logging import get_logger
from app.models.models import Job

logger = get_logger("job_source")
settings = get_settings()


class JobSourceProvider(ABC):
    source_code: str = "base"
    source_license: str = ""

    @property
    def effective_source_code(self) -> str:
        """实际写入 jobs 行的来源标识（子类可覆盖，如 O*NET 回退 curated 时）。"""
        return self.source_code

    @property
    def effective_license(self) -> str:
        """实际写入 jobs 行的许可证（须与实际载入数据一致，避免版权标注错配）。"""
        return self.source_license

    @abstractmethod
    async def _load_raw(self) -> list[dict]:
        """返回原始岗位列表（含 title/category/level/description/required_skills/soc_code）。"""

    def _load_zh_overlay(self) -> dict:
        path = settings.ZH_OVERLAY_PATH
        if not settings.JOB_SOURCE_ZH_OVERLAY or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _apply_overlay(self, raw: list[dict], overlay: dict) -> list[dict]:
        for job in raw:
            key = job.get("soc_code") or job.get("title")
            ov = overlay.get(key)
            if ov:
                job["title_zh"] = ov.get("title_zh", job.get("title_zh"))
                job["required_skills_zh"] = ov.get("skills_zh", job.get("required_skills_zh"))
                job["description_zh"] = ov.get("description_zh", job.get("description_zh"))
        return raw

    async def seed(self) -> dict:
        raw = await self._load_raw()
        overlay = self._load_zh_overlay()
        raw = self._apply_overlay(raw, overlay)
        raw = raw[: settings.JOB_SOURCE_LIMIT] if settings.JOB_SOURCE_LIMIT else raw
        if settings.JOB_SOURCE_CATEGORIES:
            cats = set(settings.JOB_SOURCE_CATEGORIES)
            raw = [j for j in raw if j.get("category") in cats]

        ingested = skipped = errors = 0
        emb_texts: list[tuple[str, str]] = []  # (id_key, text)
        rows: list[Job] = []

        async with SessionLocal() as session:
            for item in raw:
                try:
                    soc = item.get("soc_code")
                    existing = None
                    if soc:
                        existing = await session.scalar(
                            select(Job).where(Job.soc_code == soc).limit(1)
                        )
                    if existing:
                        skipped += 1
                        continue
                    job = Job(
                        source_code=self.effective_source_code,
                        source_license=self.effective_license,
                        title=item["title"],
                        title_zh=item.get("title_zh"),
                        category=item.get("category"),
                        level=item.get("level"),
                        description=item.get("description"),
                        description_zh=item.get("description_zh"),
                        required_skills=item.get("required_skills", []),
                        required_skills_zh=item.get("required_skills_zh"),
                        soc_code=soc,
                        is_seed=True,
                    )
                    session.add(job)
                    await session.flush()
                    text = self._embed_text(item)
                    emb_texts.append((str(job.id), text))
                    rows.append(job)
                    ingested += 1
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    logger.warning("job_seed_error", error=str(e), item=item.get("title"))

            # 批量嵌入（deepseek-embedding）
            if emb_texts:
                try:
                    texts = [t for _, t in emb_texts]
                    vecs = await get_llm().embed(texts)
                    id_map = {i: vecs[i] for i in range(len(vecs))}
                    for idx, (jid, _) in enumerate(emb_texts):
                        for job in rows:
                            if str(job.id) == jid:
                                job.embedding = id_map[idx]
                                break
                except Exception as e:  # noqa: BLE001
                    logger.warning("job_embed_skipped", error=str(e))
            await session.commit()

        logger.info("seed_done", provider=self.source_code, ingested=ingested,
                    skipped=skipped, errors=errors)
        return {"provider": self.source_code, "ingested": ingested,
                "skipped": skipped, "errors": errors}

    @staticmethod
    def _embed_text(item: dict) -> str:
        title = item.get("title_zh") or item.get("title") or ""
        desc = item.get("description_zh") or item.get("description") or ""
        skills = item.get("required_skills_zh") or item.get("required_skills") or []
        return f"{title}。{desc}。技能：{', '.join(skills)}"


class JsonProvider(JobSourceProvider):
    source_code = "curated"
    source_license = "proprietary"

    async def _load_raw(self) -> list[dict]:
        out: list[dict] = []
        d = settings.CURATED_JOBS_DIR
        if not os.path.isdir(d):
            return out
        for fn in os.listdir(d):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    out.extend(data)
                elif isinstance(data, dict) and "jobs" in data:
                    out.extend(data["jobs"])
        return out


class OnetProvider(JobSourceProvider):
    source_code = "onet"
    source_license = "CC BY 4.0"

    async def _load_raw(self) -> list[dict]:
        # 优先 O*NET 快照（由 data/jobs/onet/ingest.py 生成）
        snap = settings.ONET_SNAPSHOT_PATH
        if os.path.exists(snap):
            self._used_snapshot = True
            with open(snap, "r", encoding="utf-8") as f:
                return json.load(f)
        # 快照缺失：回退 curated（自有 IP），并提示运行摄入脚本
        self._used_snapshot = False
        logger.warning("onet_snapshot_missing", hint="运行 python data/jobs/onet/ingest.py 生成快照")
        return await JsonProvider()._load_raw()

    @property
    def effective_source_code(self) -> str:
        # 回退到 curated 时，实际写入的是自有 IP 数据，来源标识须同步修正
        return "curated" if getattr(self, "_used_snapshot", None) is False else "onet"

    @property
    def effective_license(self) -> str:
        # 版权标注须与实际载入数据一致：CC BY 4.0 仅当真正用了 O*NET 快照
        return "proprietary" if getattr(self, "_used_snapshot", None) is False else "CC BY 4.0"


_PROVIDER: JobSourceProvider | None = None


def get_job_source() -> JobSourceProvider:
    global _PROVIDER
    if _PROVIDER is None:
        if settings.JOB_SOURCE == "onet":
            _PROVIDER = OnetProvider()
        elif settings.JOB_SOURCE == "json":
            _PROVIDER = JsonProvider()
        else:
            raise ValueError(f"未知 JOB_SOURCE: {settings.JOB_SOURCE}")
    return _PROVIDER
