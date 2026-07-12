"""岗位匹配（§7 双引擎：向量粗排 + LLM 精排/理由）。

- 简历结构化文本 → embed → pgvector 余弦检索 top-N jobs（快）。
- 对 top-N 用 LLM 给匹配分/缺口/理由（准，可解释）；
  LLM 不可用时回退纯规则匹配（命中技能占比）。
- 写入 JobMatch 行，返回供后续查询。
"""
from __future__ import annotations

import json
import uuid
from sqlalchemy import select

from app.core.llm import LlmUnavailableError, get_llm
from app.core.logging import get_logger
from app.models.models import Job, JobMatch
from app.schemas.schemas import ResumeStructured

logger = get_logger("matcher")

TOP_K = 10  # 向量粗排取 top-10 再精排


def _resume_embed_text(s: ResumeStructured) -> str:
    parts: list[str] = []
    if s.title:
        parts.append(f"目标职位：{s.title}")
    if s.summary:
        parts.append(s.summary)
    if s.skills:
        parts.append("技能：" + "、".join(s.skills))
    if s.work_history:
        parts.append("经历：" + "；".join(s.work_history))
    if s.education:
        parts.append("教育：" + "；".join(s.education))
    return "\n".join(parts) or "简历内容"


async def match_resume(
    resume_id: uuid.UUID, structured: ResumeStructured, session
) -> list[JobMatch]:
    """返回写入后的 JobMatch 列表（已 flush，含生成的主键）。"""
    text = _resume_embed_text(structured)
    # 1) 嵌入（失败则无法向量匹配，返回空，由调用方决定降级）
    try:
        emb = (await get_llm().embed([text]))[0]
    except (LlmUnavailableError, Exception) as e:  # noqa: BLE001
        logger.warning("match_embed_failed", error=str(e), mode="skip")
        return []

    # 2) 向量粗排
    stmt = (
        select(Job)
        .where(Job.embedding.isnot(None))
        .order_by(Job.embedding.cosine_distance(emb))
        .limit(TOP_K)
    )
    top_jobs = list((await session.scalars(stmt)).all())
    if not top_jobs:
        logger.info("match_no_jobs", hint="岗位库可能未摄入或均无嵌入")
        return []

    # 3) 精排 + 理由（LLM，失败回退规则）
    results: list[JobMatch] = []
    for job in top_jobs:
        try:
            score, matched, missing, rationale = await _score_one(structured, job)
        except LlmUnavailableError:
            score, matched, missing, rationale = _rule_score(structured, job)
        except (json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            logger.warning("match_score_fallback", job=job.title, error=str(e))
            score, matched, missing, rationale = _rule_score(structured, job)
        jm = JobMatch(
            resume_id=resume_id,
            job_id=job.id,
            score=score,
            matched_skills=matched,
            missing_skills=missing,
            rationale=rationale,
        )
        session.add(jm)
        results.append(jm)
    await session.flush()
    return results


async def _score_one(structured: ResumeStructured, job: Job) -> tuple[float, list, list, str]:
    llm = get_llm()
    job_skills = job.required_skills or []
    prompt = (
        "你是招聘匹配专家。给定简历与岗位要求，评估匹配度。\n"
        f"简历技能: {structured.skills}\n"
        f"简历经历: {structured.work_history}\n"
        f"岗位: {job.title_zh or job.title}\n"
        f"岗位要求技能: {job_skills}\n"
        "返回 JSON：{\"score\": 0-100 整数, \"matched_skills\": [...], "
        "\"missing_skills\": [...], \"rationale\": \"简短中文理由\"}"
    )
    resp = await llm.chat(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    d = json.loads(resp)
    return (
        float(d.get("score", 50)),
        d.get("matched_skills", []) or [],
        d.get("missing_skills", []) or [],
        d.get("rationale", ""),
    )


def _rule_score(structured: ResumeStructured, job: Job) -> tuple[float, list, list, str]:
    js = {s.lower() for s in (job.required_skills or [])}
    rs = {s.lower() for s in (structured.skills or [])}
    matched = [s for s in (job.required_skills or []) if s.lower() in rs]
    missing = [s for s in (job.required_skills or []) if s.lower() not in rs]
    score = round(100 * len(matched) / len(js), 1) if js else 50.0
    rationale = (
        f"规则匹配：命中 {len(matched)}/{len(js)} 项要求技能"
        if js
        else "岗位无明确技能要求，按中性分计"
    )
    return float(score), matched, missing, rationale
