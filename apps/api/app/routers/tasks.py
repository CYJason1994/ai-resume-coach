"""任务状态查询：所有读路径需携带 access_token 并校验（R2-C2）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.deps import require_access_token
from app.core.db import SessionLocal
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.security import verify_token
from app.models.models import Resume, Task
from app.schemas.schemas import TaskStatus

logger = get_logger("tasks")
router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatus)
async def get_task(task_id: uuid.UUID, token: str = Depends(require_access_token)):
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise NotFoundError("Task", str(task_id))
        resume = await session.get(Resume, task.resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限查看该任务")
        return TaskStatus(
            task_id=task.id,
            type=task.type,
            status=task.status,
            progress=task.progress,
            error_text=task.error_text,
            updated_at=task.updated_at,
        )
