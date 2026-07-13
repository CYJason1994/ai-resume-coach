"""M3 模拟面试官：会话创建 + SSE 流式对话 + 结束评估 + 查询。读/写均需 token（R2-C2）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import require_access_token
from app.core.db import SessionLocal
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.security import verify_token
from app.models.models import InterviewSession, Job, Resume
from app.schemas.schemas import (
    ChatMessage,
    FeedbackItem,
    SessionCreateRequest,
    SessionMessageRequest,
    SessionOverall,
    SessionView,
)
from app.services.interview_coach import overall_evaluate, stream_session_reply

logger = get_logger("interview_sessions")
router = APIRouter(prefix="/api/interview-sessions", tags=["interview-sessions"])


def _msg_from_dict(m: dict) -> ChatMessage:
    fb = m.get("feedback")
    feedback = (
        FeedbackItem(
            score=int(fb.get("score", 0)),
            strengths=list(fb.get("strengths", [])),
            improvements=list(fb.get("improvements", [])),
            dimension=str(fb.get("dimension", "mixed")),
        )
        if fb
        else None
    )
    return ChatMessage(role=m["role"], content=m["content"], feedback=feedback)


def _to_view(sess: InterviewSession, job_title: str) -> SessionView:
    return SessionView(
        session_id=str(sess.id),
        resume_id=str(sess.resume_id),
        job_id=str(sess.job_id),
        job_title=job_title,
        dimension_focus=sess.dimension_focus,
        status=sess.status,
        transcript=[_msg_from_dict(m) for m in (sess.transcript or [])],
        overall_score=sess.overall_score,
    )


@router.post("", response_model=SessionView)
async def create_session(
    body: SessionCreateRequest, token: str = Depends(require_access_token)
):
    rid = body.resume_id
    async with SessionLocal() as session:
        resume = await session.get(Resume, rid)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限")
        job = await session.get(Job, body.job_id)
        if not job:
            raise NotFoundError("Job", str(body.job_id))
        job_title = job.title_zh or job.title

        sess = InterviewSession(
            resume_id=rid,
            job_id=body.job_id,
            interview_task_id=body.interview_task_id,
            dimension_focus=body.dimension_focus or "mixed",
            mode=body.mode or "freeform",
            status="active",
            transcript=[],
            summary_text="",
            overall_score=None,
        )
        session.add(sess)
        await session.flush()
        resp = _to_view(sess, job_title)
        await session.commit()
        return resp


@router.post("/{session_id}/message")
async def session_message(
    session_id: uuid.UUID,
    body: SessionMessageRequest,
    token: str = Depends(require_access_token),
) -> StreamingResponse:
    # 先校验归属（HTTP 错误需在返回 StreamingResponse 前抛出）
    async with SessionLocal() as session:
        sess = await session.get(InterviewSession, session_id)
        if not sess:
            raise NotFoundError("InterviewSession", str(session_id))
        resume = await session.get(Resume, sess.resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限查看该会话")

    # 校验通过；用独立 session 驱动流式（保持连接期间会话开启）
    async def event_stream():
        async with SessionLocal() as db:
            s = await db.get(InterviewSession, session_id)
            async for ev in stream_session_reply(db, s, body.message):
                yield ev

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/finish", response_model=SessionOverall)
async def finish_session(
    session_id: uuid.UUID, token: str = Depends(require_access_token)
):
    async with SessionLocal() as session:
        sess = await session.get(InterviewSession, session_id)
        if not sess:
            raise NotFoundError("InterviewSession", str(session_id))
        resume = await session.get(Resume, sess.resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限查看该会话")
        # P2-4 幂等：已结束且已有整体评估时直接返回，避免重复 LLM 调用（双倍成本/分数漂移）
        if sess.status == "finished" and sess.overall_score:
            o = sess.overall_score
            return SessionOverall(
                session_id=str(sess.id),
                status=sess.status,
                overall_score=o.get("overall_score"),
                summary=o.get("summary"),
                top_strengths=o.get("top_strengths", []),
                top_gaps=o.get("top_gaps", []),
                suggestion=o.get("suggestion"),
            )
        overall = await overall_evaluate(session, sess)
        return SessionOverall(
            session_id=str(sess.id),
            status=sess.status,
            overall_score=overall.get("overall_score"),
            summary=overall.get("summary"),
            top_strengths=overall.get("top_strengths", []),
            top_gaps=overall.get("top_gaps", []),
            suggestion=overall.get("suggestion"),
        )


@router.get("/{session_id}", response_model=SessionView)
async def get_session(
    session_id: uuid.UUID, token: str = Depends(require_access_token)
):
    async with SessionLocal() as session:
        sess = await session.get(InterviewSession, session_id)
        if not sess:
            raise NotFoundError("InterviewSession", str(session_id))
        resume = await session.get(Resume, sess.resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限查看该会话")
        job = await session.get(Job, sess.job_id)
        job_title = job.title_zh or job.title if job else ""
        return _to_view(sess, job_title)
