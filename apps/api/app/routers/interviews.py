"""面试题目生成（M2）：生成触发 + 结果查询。读路径均需 token（R2-C2）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import require_access_token
from app.core.db import SessionLocal
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.security import verify_token
from app.models.models import InterviewQuestion, Job, Resume, Task
from app.schemas.schemas import (
    InterviewGenerateRequest,
    InterviewListResponse,
    InterviewQuestionItem,
)
from app.workers.tasks import enqueue_generate_interview

logger = get_logger("interviews")
router = APIRouter(prefix="/api/interviews", tags=["interviews"])


@router.post("/generate", response_model=InterviewListResponse)
async def generate_interview(
    body: InterviewGenerateRequest,
    token: str = Depends(require_access_token),
):
    rid = body.resume_id
    jid = body.job_id
    async with SessionLocal() as session:
        resume = await session.get(Resume, rid)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限")
        job = await session.get(Job, jid)
        if not job:
            raise NotFoundError("Job", str(jid))
        # 创建任务并投递 ARQ（重活异步）
        task = Task(resume_id=rid, type="interview", status="pending")
        session.add(task)
        await session.flush()
        task_id = task.id
        job_title = job.title_zh or job.title
    await enqueue_generate_interview(task_id, rid, jid)
    logger.info("interview_enqueued", task_id=str(task_id), job_id=str(jid))
    return InterviewListResponse(
        task_id=str(task_id),
        resume_id=str(rid),
        job_id=str(jid),
        job_title=job_title,
        status="pending",
        degraded=False,
        questions=[],
    )


@router.get("/{task_id}", response_model=InterviewListResponse)
async def get_interview(
    task_id: uuid.UUID, token: str = Depends(require_access_token)
):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise NotFoundError("Task", str(task_id))
        resume = await session.get(Resume, task.resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限查看该结果")

        rows = list(
            (
                await session.execute(
                    select(InterviewQuestion)
                    .where(InterviewQuestion.resume_id == resume.id)
                    .order_by(InterviewQuestion.order_index)
                )
            )
            .scalars()
            .all()
        )
        job_title = rows[0].job_title if rows else ""
        # 降级信息记录在 error_text（status 仍为 done）
        degraded = bool(task.error_text and "规则模板" in (task.error_text or ""))
        return InterviewListResponse(
            task_id=str(task.id),
            resume_id=str(resume.id),
            job_id=str(rows[0].job_id) if rows else "",
            job_title=job_title,
            status=task.status,
            degraded=degraded,
            error_text=task.error_text,
            questions=[
                InterviewQuestionItem(
                    id=str(q.id),
                    dimension=q.dimension,
                    question=q.question,
                    expected_focus=q.expected_focus,
                    difficulty=q.difficulty,
                    order_index=q.order_index,
                )
                for q in rows
            ],
        )
