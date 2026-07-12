"""结果查询：返回结构化 + 匹配岗位（R2-C2 匿名 token 校验）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.api.deps import require_access_token
from app.core.db import SessionLocal
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.security import verify_token
from app.models.models import Job, JobMatch, Resume, ResumeParse, Task
from app.schemas.schemas import MatchItem, ResumeResultResponse, ResumeStructured

logger = get_logger("result")
router = APIRouter(tags=["result"])


@router.get("/tasks/{task_id}/result", response_model=ResumeResultResponse)
async def get_result(task_id: uuid.UUID, token: str = Depends(require_access_token)):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise NotFoundError("Task", str(task_id))
        resume = await session.get(Resume, task.resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限查看该结果")

        # 最新解析
        parse = (
            await session.scalars(
                select(ResumeParse)
                .where(ResumeParse.resume_id == resume.id)
                .order_by(ResumeParse.created_at.desc())
            )
        ).first()
        structured = (
            ResumeStructured(**(parse.structured_data or {}))
            if parse and parse.structured_data
            else None
        )

        # 匹配（join 取岗位标题，按分数降序）
        rows = (
            await session.execute(
                select(JobMatch, Job)
                .join(Job, Job.id == JobMatch.job_id)
                .where(JobMatch.resume_id == resume.id)
                .order_by(JobMatch.score.desc())
            )
        ).all()
        matches = [
            MatchItem(
                job_id=str(m.job_id),
                title=job.title,
                title_zh=job.title_zh,
                category=job.category,
                score=m.score or 0.0,
                matched_skills=m.matched_skills or [],
                missing_skills=m.missing_skills or [],
                rationale=m.rationale,
            )
            for m, job in rows
        ]

        return ResumeResultResponse(
            task_id=str(task.id),
            resume_id=str(resume.id),
            status=task.status,
            structured=structured,
            matches=matches,
        )
