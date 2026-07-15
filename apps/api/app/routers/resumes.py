"""合规删除：软删简历 + 删除文件 + 失效令牌（R2-C2 匿名 token 校验）。

§7.6 PIPL：用户应可随时删除其简历（含原始文件与结构化数据）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.deps import require_access_token
from app.core.db import SessionLocal
from app.core.errors import ForbiddenError, NotFoundError
from app.core.audit import anonymized_subject, audit_event
from app.core.logging import get_logger
from app.core.security import verify_token
from app.models.models import Resume
from app.services.compliance import purge_resume_personal_data
from app.services.storage import get_storage

logger = get_logger("resumes")
router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.delete("/{resume_id}")
async def delete_resume(
    resume_id: uuid.UUID,
    request: Request,
    token: str = Depends(require_access_token),
):
    async with SessionLocal() as session:
        resume = await session.get(Resume, resume_id)
        if not resume or not verify_token(token, resume.access_token_hash):
            raise ForbiddenError("访问令牌无效或无权限删除该简历")
        try:
            await get_storage().delete(resume.storage_key, resume.file_type)
        except Exception as e:  # noqa: BLE001
            logger.warning("delete_file_failed", error=str(e))
        # 级联擦除个人数据（PIPL 删除权）：解析行 + 匹配行
        purge = await purge_resume_personal_data(session, resume_id)
        logger.info("resume_purged", **purge)
        resume.deleted_at = datetime.now(timezone.utc)
        resume.access_token_hash = ""  # 失效令牌
        await session.commit()
    logger.info("resume_deleted", resume_id=str(resume_id))
    audit_event(
        "resume.delete",
        subject=anonymized_subject(token),
        resource="resume",
        resource_id=str(resume_id),
        request_id=getattr(request.state, "request_id", None),
    )
    return {"deleted": True, "resume_id": str(resume_id)}
