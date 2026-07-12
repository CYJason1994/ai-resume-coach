"""面试题目生成（M2，§7.5）。

- 输入：简历结构化画像 + 目标岗位 → DeepSeek 生成分维度面试题。
- 维度：behavioral（行为）/ technical（技术）/ role（岗位匹配）/ stress（压力）。
- 上送遵循白名单（§7.3）：仅技能/经验/教育/摘要 + 岗位信息，**绝不**带上送 PII。
- 降级：LLM 不可用时回退规则模板（基于岗位必备技能 + 简历技能），
  保证 MVP 无 DeepSeek Key 也能演示（`generate_questions` 返回 degraded=True）。
- 幂等：生成前清空该 (resume_id, job_id) 旧题，重生成不重复累积。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LlmUnavailableError
from app.core.llm import get_llm
from app.core.logging import get_logger
from app.models.models import InterviewQuestion, Job
from app.schemas.schemas import ResumeStructured

logger = get_logger("interview_gen")

# 维度英文 key（持久化）+ 中文展示标签（仅前端用，后端只存 key）
DIMENSIONS = ["behavioral", "technical", "role", "stress"]
DIMENSION_LABELS = {
    "behavioral": "行为面试",
    "technical": "技术面试",
    "role": "岗位匹配",
    "stress": "压力面试",
}
PER_DIMENSION = 3  # 每维度建议题量（LLM 可上下浮动，规则模板另定）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_messages(structured: ResumeStructured, job: Job) -> list[dict]:
    """构造白名单 payload：仅岗位相关 + 简历画像（已脱敏），剥离一切 PII。"""
    prof = {
        "skills": structured.skills or [],
        "experience_years": structured.experience_years,
        "education": structured.education or [],
        "summary": structured.summary,
        "work_history": structured.work_history or [],
        "projects": structured.projects or [],
    }
    job_info = {
        "title": job.title,
        "title_zh": job.title_zh,
        "category": job.category,
        "required_skills": job.required_skills or [],
        "description": (job.description or "")[:800],
    }
    dim_labels = ", ".join(f"{d}={DIMENSION_LABELS[d]}" for d in DIMENSIONS)
    system = (
        "你是资深技术面试官。根据候选人简历画像与目标岗位，生成分维度面试题。"
        f"必须覆盖四个维度：{dim_labels}。每个维度至少 {PER_DIMENSION} 题。"
        "每题包含字段：dimension(上述英文 key 之一)、question(具体问题文本)、"
        "expected_focus(本题考察点)、difficulty(junior|mid|senior)。"
        "只输出 JSON，结构：{\"questions\":[...]}。不要解释，不要 markdown 代码块。"
    )
    user = (
        "候选人画像（已脱敏）：\n"
        + json.dumps(prof, ensure_ascii=False, indent=2)
        + "\n\n目标岗位：\n"
        + json.dumps(job_info, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_llm_json(text: str) -> list[dict]:
    """从 LLM 输出解析题目列表（容错：去 ```json 包裹、裁剪首尾噪声）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        parts = raw.split("```", 2)
        raw = parts[1] if len(parts) > 1 else raw
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e != -1 and e > s:
            data = json.loads(raw[s : e + 1])
        else:
            raise
    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("LLM 返回缺少 questions 数组")
    return items


def _rule_questions(structured: ResumeStructured, job: Job) -> list[dict]:
    """规则兜底（无 LLM / 解析失败）：基于岗位必备技能 + 简历技能的模板题。"""
    skills = list(job.required_skills or []) or list(structured.skills or [])
    top_skills = skills[:3]
    yrs = structured.experience_years
    title = job.title_zh or job.title
    qs: list[dict] = []

    # behavioral
    qs.append({"dimension": "behavioral", "question": "请描述一次你在项目中与同事发生重大分歧的经历，你是如何推动达成共识的？", "expected_focus": "冲突解决与沟通协作", "difficulty": "mid"})
    qs.append({"dimension": "behavioral", "question": "分享一个你主动承担、超出职责范围且取得成果的事项。", "expected_focus": "主动性与主人翁意识", "difficulty": "mid"})
    qs.append({"dimension": "behavioral", "question": "讲讲你最近一次从失败中复盘并改进的完整经历。", "expected_focus": "复盘与成长型思维", "difficulty": "junior"})

    # technical
    if top_skills:
        for sk in top_skills:
            qs.append({"dimension": "technical", "question": f"请结合具体项目，讲讲你在「{sk}」上的实践经验与踩过的坑。", "expected_focus": f"{sk} 的实战深度", "difficulty": "mid"})
    else:
        qs.append({"dimension": "technical", "question": f"针对「{title}」岗位，你认为最核心的 3 项技术能力是什么？你如何证明自己具备？", "expected_focus": "技术栈与岗位契合", "difficulty": "mid"})
    qs.append({"dimension": "technical", "question": "描述一次你排查并解决一个棘手技术问题的完整过程。", "expected_focus": "问题定位与系统性", "difficulty": "senior"})

    # role
    qs.append({"dimension": "role", "question": f"你为什么认为自己适合「{title}」这个岗位？请用过往经历佐证。", "expected_focus": "岗位动机与匹配度", "difficulty": "junior"})
    qs.append({"dimension": "role", "question": f"如果入职「{title}」，你前 90 天的规划是什么？", "expected_focus": "岗位理解与落地规划", "difficulty": "senior"})
    if yrs:
        qs.append({"dimension": "role", "question": f"以你 {yrs} 年的经验，你如何在该岗位上建立差异化优势？", "expected_focus": "经验变现与价值创造", "difficulty": "senior"})

    # stress
    qs.append({"dimension": "stress", "question": "如果你的方案在评审会上被资深同事当场否决，你会如何应对？", "expected_focus": "抗压与情绪管理", "difficulty": "mid"})
    qs.append({"dimension": "stress", "question": "设想一个你完全不熟悉的紧急任务，你会在 24 小时内如何破局？", "expected_focus": "未知问题处理", "difficulty": "senior"})
    qs.append({"dimension": "stress", "question": "当业务方临时把交付期提前一倍，你会怎么处理？", "expected_focus": "优先级与资源协调", "difficulty": "mid"})
    return qs


async def generate_questions(
    resume_id: uuid.UUID,
    structured: ResumeStructured,
    job: Job,
    session: AsyncSession,
) -> tuple[list[InterviewQuestion], bool]:
    """生成并持久化面试题，返回 (题目行, 是否走规则兜底)。

    生成前先清空该 (resume_id, job_id) 旧题，保证幂等可重生成。
    """
    # 幂等：清旧
    await session.execute(
        delete(InterviewQuestion).where(
            InterviewQuestion.resume_id == resume_id,
            InterviewQuestion.job_id == job.id,
        )
    )

    degraded = False
    raw_items: list[dict] = []
    try:
        text = await get_llm().chat(
            _build_messages(structured, job),
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        raw_items = _parse_llm_json(text)
        # 仅保留合法维度，过滤模型乱填
        raw_items = [it for it in raw_items if isinstance(it, dict) and it.get("dimension") in DIMENSIONS]
    except (LlmUnavailableError, ValueError) as e:
        logger.warning("interview_llm_failed", error=str(e), mode="rule_fallback")
        degraded = True
        raw_items = _rule_questions(structured, job)

    rows: list[InterviewQuestion] = []
    order = 0
    for it in raw_items:
        q = (it.get("question") or "").strip()
        if not q:
            continue
        rows.append(
            InterviewQuestion(
                resume_id=resume_id,
                job_id=job.id,
                job_title=job.title_zh or job.title,
                dimension=str(it.get("dimension") or "behavioral"),
                question=q,
                expected_focus=str(it.get("expected_focus") or "").strip() or None,
                difficulty=str(it.get("difficulty") or "").strip() or None,
                order_index=order,
            )
        )
        order += 1
    for r in rows:
        session.add(r)
    await session.flush()
    logger.info("interview_generated", count=len(rows), degraded=degraded)
    return rows, degraded
