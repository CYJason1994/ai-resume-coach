"""岗位匹配（§7 双引擎：向量粗排 + LLM 精排/理由，无 Key 时纯规则兜底）。

- 简历结构化文本 → embed → pgvector 余弦检索 top-N jobs（快）。
- 对 top-N 用 LLM 给匹配分/缺口/理由（准，可解释）；
  LLM 不可用时回退纯规则匹配（命中技能占比）。
- embed 不可用（无 Key / 网络失败 / 岗位库无向量）时，直接走纯规则匹配
  对全部岗位打分取 top-K，保证无 DeepSeek Key 也能产出匹配。
- 写入 JobMatch 行，返回供后续查询。
"""
from __future__ import annotations

import json
import uuid
from sqlalchemy import select
# SQLAlchemy 2.0 以 `insert` 暴露 PG 专属 upsert（pg_insert 为旧别名，部分 stub 未声明）
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.llm import get_llm, reset_llm_cost_key, set_llm_cost_key
from app.core.logging import get_logger
from app.models.models import Job, JobMatch
from app.schemas.schemas import ResumeStructured

logger = get_logger("matcher")

TOP_K = 10  # 向量粗排取 top-10 再精排


def _resume_embed_text(s: ResumeStructured) -> str:
    """构造嵌入文本（PII 白名单，P2-5）：仅技能/摘要/教育/职位，
    不含 work_history / projects（可能含公司名等 PII，不得进入向量库）。"""
    parts: list[str] = []
    if s.title:
        parts.append(f"目标职位：{s.title}")
    if s.summary:
        parts.append(s.summary)
    if s.skills:
        parts.append("技能：" + "、".join(s.skills))
    if s.education:
        parts.append("教育：" + "；".join(s.education))
    return "\n".join(parts) or "简历内容"


async def match_resume(
    resume_id: uuid.UUID, structured: ResumeStructured, session
) -> list[JobMatch]:
    """返回写入后的 JobMatch 列表（已 flush，含生成的主键）。

    匹配策略（双引擎，失败隔离）：
    - 嵌入可用 → 向量粗排 top-K → LLM 精排/理由（LLM 失败回退规则）。
    - 嵌入不可用（无 Key / 网络失败 / 岗位库无向量）→ 纯规则匹配兜底，
      对全部岗位按技能重叠度打分取 top-K。保证 MVP 在无 DeepSeek Key 时
      仍能产出匹配（与 v0.3「整条链路不崩」承诺一致）。
    """
    # P1-6：以 resume_id 作为成本核算键，单次简历匹配受单份成本上限约束
    tok = set_llm_cost_key(str(resume_id))
    try:
        text = _resume_embed_text(structured)
        # 1) 嵌入（失败则降级为纯规则匹配，而非整段放弃）
        try:
            emb = (await get_llm().embed([text]))[0]
        except Exception as e:  # noqa: BLE001
            logger.warning("match_embed_unavailable", error=str(e), mode="rule_fallback")
            return await _rule_match_all(resume_id, structured, session)

        # 2) 向量粗排
        stmt = (
            select(Job)
            .where(Job.embedding.isnot(None))
            .order_by(Job.embedding.cosine_distance(emb))
            .limit(TOP_K)
        )
        top_jobs = list((await session.scalars(stmt)).all())
        if not top_jobs:
            # 岗位库无嵌入向量（如 seed 时未配置 Key）→ 回退纯规则匹配
            logger.info("match_no_embeddings", hint="岗位库无嵌入向量，回退纯规则匹配")
            return await _rule_match_all(resume_id, structured, session)

        # 3) 精排 + 理由（LLM，失败回退规则）
        results: list[JobMatch] = []
        for job in top_jobs:
            try:
                score, matched, missing, rationale = await _score_one(structured, job)
            except Exception as e:  # noqa: BLE001
                logger.warning("match_score_fallback", job=job.title, error=str(e))
                score, matched, missing, rationale = _rule_score(structured, job)
            results.append(
                JobMatch(
                    resume_id=resume_id,
                    job_id=job.id,
                    score=score,
                    matched_skills=matched,
                    missing_skills=missing,
                    rationale=rationale,
                )
            )
        # P2-4：幂等 upsert（同 resume_id/job_id 重复投递不重复累积）
        await _persist_matches(session, results)
        return results
    finally:
        reset_llm_cost_key(tok)


async def _rule_match_all(
    resume_id: uuid.UUID, structured: ResumeStructured, session, top_k: int = TOP_K
) -> list[JobMatch]:
    """无嵌入时的纯规则匹配：对全部岗位按技能重叠度打分，取 top-K 写入。"""
    jobs = list(
        (await session.scalars(select(Job).where(Job.required_skills.isnot(None)))).all()
    )
    if not jobs:
        logger.info("rule_match_no_jobs", hint="岗位库为空或不含技能要求")
        return []
    scored = [
        (score, job, matched, missing, rationale)
        for job in jobs
        for (score, matched, missing, rationale) in [_rule_score(structured, job)]
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    results: list[JobMatch] = []
    for score, job, matched, missing, rationale in scored[:top_k]:
        results.append(
            JobMatch(
                resume_id=resume_id,
                job_id=job.id,
                score=score,
                matched_skills=matched,
                missing_skills=missing,
                rationale=rationale,
            )
        )
    # P2-4：幂等 upsert（同 resume_id/job_id 重复投递不重复累积）
    await _persist_matches(session, results)
    return results


async def _persist_matches(session, matches: list[JobMatch]) -> None:
    """幂等写入 JobMatch（P2-4）。

    真实 DB 走 pg_insert upsert（依赖 JobMatch 上的 (resume_id, job_id) 唯一约束）；
    无法执行 `execute` 的测试替身退回普通 `add`（保持旧行为，便于单测）。
    """
    if not matches:
        return
    try:
        stmt = pg_insert(JobMatch).values(
            [
                {
                    "resume_id": m.resume_id,
                    "job_id": m.job_id,
                    "score": m.score,
                    "matched_skills": m.matched_skills,
                    "missing_skills": m.missing_skills,
                    "rationale": m.rationale,
                }
                for m in matches
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["resume_id", "job_id"],
            set_={
                "score": stmt.excluded.score,
                "matched_skills": stmt.excluded.matched_skills,
                "missing_skills": stmt.excluded.missing_skills,
                "rationale": stmt.excluded.rationale,
            },
        )
        await session.execute(stmt)
    except Exception:  # noqa: BLE001 — 无 execute（测试替身）时退回 add
        for m in matches:
            session.add(m)
    await session.flush()


async def _score_one(structured: ResumeStructured, job: Job) -> tuple[float, list, list, str]:
    """LLM 精排打分（单租户、临时调用）。

    设计决策（收口自对抗审查遗留项）：此处**有意**包含 `work_history`，
    与 P2-5「嵌入载荷去 work_history」并不冲突——
    - 嵌入向量写入的是**多租户、持久化**的 pgvector 库，必须杜绝 PII 泄漏；
    - 本打分是**单租户、临时性**的 LLM 调用，仅用用户*自己*的简历数据为其本人
      生成匹配结果，且 `work_history` 属于 M1 PII 白名单中的 `experiences`（允许发 LLM）。
    若未来要求评分也做最强 PII 最小化，移除下一行的 work_history 引用即可
    （会牺牲一定匹配质量，按合规需求取舍）。
    """
    llm = get_llm()
    job_skills = job.required_skills or []
    prompt = (
        "你是招聘匹配专家。给定简历与岗位要求，评估匹配度。\n"
        f"简历技能: {structured.skills}\n"
        # 见函数 docstring：work_history 此处为有意包含（单租户临时调用，属 PII 白名单）
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
