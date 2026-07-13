"""M3 模拟面试官：上下文管理 + 流式追问 + 逐轮评分 + 整体评估（§7.5）。

设计要点：
- `build_context`：系统提示（面试官人设 + 岗位 + 简历画像 + 维度聚焦 + 题库种子）
  + 周期摘要 `summary_text` + 滚动窗口（最近 `MAX_WINDOW_TURNS` 条），**防止上下文溢出 / 控成本**。
- `stream_session_reply`：SSE 友好的异步生成器，逐片 yield `token` 事件；末尾用非流式
  `json_object` 调用产出该轮评分（`feedback` 事件）；评分归属到用户本轮回答。
- `overall_evaluate`：结束时整体评估。
- 复用 `get_llm()` 信号量 / 预算 / 降级全链路；无 Key / 降级冷却时抛 `LlmUnavailableError`
  → 端点转为 `error` 事件，前端清晰降级（不白屏不崩）。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LlmUnavailableError
from app.core.llm import get_llm
from app.core.logging import get_logger
from app.models.models import InterviewQuestion, InterviewSession, Job, Resume, ResumeParse

logger = get_logger("interview_coach")

# ── 上下文管理参数（§7.5：滚动窗口 / 周期摘要 防超上下文）──
MAX_WINDOW_TURNS = 8       # 滚动窗口保留最近 N 条消息
SUMMARY_EVERY = 6          # 每累计 N 条消息后重新压缩摘要
DIMENSION_LABELS = {
    "behavioral": "行为面试",
    "technical": "技术面试",
    "role": "岗位匹配",
    "stress": "压力面试",
    "mixed": "综合",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sse(payload: dict) -> str:
    """单条 SSE 事件：`data: {json}\n\n`（前端按 `\n\n` 切分后解析 `data:` 负载）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _resume_summary_text(parse: ResumeParse | None) -> str:
    sd = (parse.structured_data or {}) if parse else {}
    parts: list[str] = []
    if sd.get("title"):
        parts.append(f"目标职位：{sd['title']}")
    if sd.get("experience_years") is not None:
        parts.append(f"经验年限：{sd['experience_years']} 年")
    if sd.get("skills"):
        parts.append("技能：" + "、".join(sd["skills"]))
    if sd.get("education"):
        parts.append("教育：" + "、".join(sd["education"]))
    if sd.get("summary"):
        parts.append("摘要：" + sd["summary"])
    return "；".join(parts) or "（简历结构化信息缺失）"


def _system_prompt(
    sess: InterviewSession, job: Job, resume_summary: str, script_questions: list[str]
) -> str:
    focus = DIMENSION_LABELS.get(sess.dimension_focus or "mixed", "综合")
    skills = "、".join(job.required_skills or []) or "（未指定）"
    script_hint = ""
    if script_questions:
        script_hint = (
            "\n建议覆盖的题库（可自然融入，不必逐条念，可改编或追问）：\n- "
            + "\n- ".join(script_questions)
        )
    return (
        "你是一位资深技术面试官，正在对候选人进行模拟面试。\n"
        f"目标岗位：{job.title_zh or job.title}。\n"
        f"岗位必备技能：{skills}。\n"
        f"候选人简历画像：{resume_summary}。\n"
        f"本次面试聚焦维度：{focus}。\n"
        "要求：\n"
        "1. 用简体中文，语气专业、友好但保持面试官的严谨。\n"
        "2. 每轮只提一个问题或一句自然的追问，不要一次性抛出多个问题。\n"
        "3. 基于候选人上一轮回答进行针对性追问或深挖，避免重复。\n"
        "4. 控制节奏，循序渐进。\n"
        "5. 只输出面试官的话，不要输出评分或分析（评分由系统单独生成）。"
        + script_hint
    )


def build_context(
    sess: InterviewSession, job: Job, transcript: list[dict], resume_summary: str, script_questions: list[str]
) -> list[dict]:
    """构造发送给 LLM 的 messages：系统提示 + 可选摘要 + 滚动窗口。

    transcript 由调用方传入（含本轮用户消息），避免在降级错误路径污染 sess.transcript。
    """
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt(sess, job, resume_summary, script_questions)}
    ]
    if sess.summary_text:
        messages.append(
            {"role": "system", "content": f"【对话摘要，供参考】{sess.summary_text}"}
        )
    window = (transcript or [])[-MAX_WINDOW_TURNS:]
    for m in window:
        messages.append({"role": m["role"], "content": m["content"]})
    return messages


async def _latest_parse(db: AsyncSession, resume_id) -> ResumeParse | None:
    res = await db.execute(
        select(ResumeParse)
        .where(ResumeParse.resume_id == resume_id)
        .order_by(ResumeParse.created_at.desc())
        .limit(1)
    )
    return res.scalars().first()


async def _load_script(db: AsyncSession, sess: InterviewSession) -> list[str]:
    if not sess.interview_task_id:
        return []
    res = await db.execute(
        select(InterviewQuestion)
        .where(InterviewQuestion.task_id == sess.interview_task_id)
        .order_by(InterviewQuestion.order_index)
    )
    return [r.question for r in res.scalars().all()]


async def _score_answer(llm, user_message: str, reply: str) -> dict:
    """非流式结构化评分：分数 / 亮点 / 改进点 / 维度。失败返回空评分（不阻断对话）。"""
    prompt = [
        {"role": "system", "content": "你是面试评分助手。基于候选人的回答与面试官的问题，给出客观、具体的评分。"},
        {
            "role": "user",
            "content": (
                "候选人本轮回答：" + user_message + "\n"
                "面试官本轮回应：" + reply + "\n"
                '请仅输出 JSON：{"score": 0-100 的整数, "strengths": ["亮点"], '
                '"improvements": ["可改进点"], "dimension": "behavioral|technical|role|stress"}'
            ),
        },
    ]
    try:
        raw = await llm.chat(prompt, response_format={"type": "json_object"})
        data = json.loads(raw)
        return {
            "score": int(data.get("score", 0)),
            "strengths": list(data.get("strengths", [])),
            "improvements": list(data.get("improvements", [])),
            "dimension": str(data.get("dimension", "mixed")),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("interview_score_failed", error=str(e))
        return {"score": 0, "strengths": [], "improvements": ["（评分暂不可用）"], "dimension": "mixed"}


async def _summarize(llm, transcript: list[dict]) -> str:
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in transcript)
    try:
        return await llm.chat(
            [
                {
                    "role": "system",
                    "content": "用 2-3 句话概括以下面试对话的关键要点（候选人的能力亮点、待深挖点、已覆盖话题），"
                    "用于后续上下文压缩。只输出概括文本。",
                },
                {"role": "user", "content": convo},
            ]
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("interview_summarize_failed", error=str(e))
        return ""


async def stream_session_reply(
    db: AsyncSession, sess: InterviewSession, user_message: str
) -> AsyncIterator[str]:
    """SSE 友好生成器：逐片 yield 面试官回复，末尾产出该轮评分并持久化。

    事件类型（JSON，统一 `{"type": ..., "value": ...}`）：
    - `token`：回复文本增量（前端追加到当前助手消息）
    - `feedback`：该轮评分 `{score,strengths,improvements,dimension}`（归属用户本轮回答）
    - `done`：本轮结束
    - `error`：降级/异常（无 Key、冷却中），不写入助手消息

    异常（LlmUnavailableError）被转换为 `error` 事件，保证前端不白屏。
    """
    transcript = list(sess.transcript or [])
    transcript.append({"role": "user", "content": user_message, "feedback": None})

    resume = await db.get(Resume, sess.resume_id)
    job = await db.get(Job, sess.job_id)
    parse = await _latest_parse(db, sess.resume_id)
    resume_summary = _resume_summary_text(parse)
    script_questions = await _load_script(db, sess)

    # transcript 含本轮用户消息，确保模型可见；仅在成功轮末才写回 sess.transcript
    messages = build_context(sess, job, transcript, resume_summary, script_questions)
    llm = get_llm()

    try:
        reply_parts: list[str] = []
        async for chunk in llm.stream_chat(messages):
            reply_parts.append(chunk)
            yield _sse({"type": "token", "value": chunk})
    except LlmUnavailableError as e:
        logger.warning("interview_stream_degraded", session_id=str(sess.id), error=str(e))
        yield _sse({"type": "error", "value": "AI 面试官暂不可用，请稍后再试。"})
        return

    reply = "".join(reply_parts)
    feedback = await _score_answer(llm, user_message, reply)

    # 评分归属用户本轮回答；追加助手回复
    transcript[-1]["feedback"] = feedback
    transcript.append({"role": "assistant", "content": reply})
    sess.transcript = transcript

    # 周期摘要（上下文压缩）
    if len(transcript) >= SUMMARY_EVERY:
        sess.summary_text = await _summarize(llm, transcript)

    sess.updated_at = _now()
    await db.commit()
    yield _sse({"type": "feedback", "value": feedback})
    yield _sse({"type": "done"})


async def overall_evaluate(db: AsyncSession, sess: InterviewSession) -> dict:
    """结束时整体评估：整体分 / 综述 / 亮点 / 短板 / 建议。失败返回空评估（不阻断）。"""
    transcript = sess.transcript or []
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in transcript)
    llm = get_llm()
    prompt = [
        {"role": "system", "content": "你是面试评估专家。基于完整模拟面试对话，给出整体评估与可执行建议。"},
        {
            "role": "user",
            "content": (
                "面试对话：\n" + convo + "\n\n"
                '请仅输出 JSON：{"overall_score": 0-100 的整数, "summary": "总体评价", '
                '"top_strengths": ["..."], "top_gaps": ["..."], "suggestion": "下一步建议"}'
            ),
        },
    ]
    try:
        raw = await llm.chat(prompt, response_format={"type": "json_object"})
        data = json.loads(raw)
        overall = {
            "overall_score": int(data.get("overall_score", 0)),
            "summary": str(data.get("summary", "")),
            "top_strengths": list(data.get("top_strengths", [])),
            "top_gaps": list(data.get("top_gaps", [])),
            "suggestion": str(data.get("suggestion", "")),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("interview_overall_failed", error=str(e))
        overall = {
            "overall_score": 0,
            "summary": "（评估暂不可用）",
            "top_strengths": [],
            "top_gaps": [],
            "suggestion": "",
        }
    sess.overall_score = overall
    sess.status = "finished"
    sess.updated_at = _now()
    await db.commit()
    return overall
