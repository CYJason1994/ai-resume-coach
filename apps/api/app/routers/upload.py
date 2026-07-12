"""上传接口：校验（扩展名+魔数+大小）→ 落盘 → 建 Resume/Task → 签发 access_token → 投 ARQ。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, File, UploadFile

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.errors import ValidationError
from app.core.logging import bind_request_id, get_logger
from app.core.security import generate_access_token, hash_token
from app.models.models import Resume, Task
from app.schemas.schemas import ALLOWED_EXTENSIONS, ALLOWED_MAGIC_PREFIXES, UploadResponse
from app.services.storage import get_storage
from app.workers.tasks import enqueue_process_resume

settings = get_settings()
logger = get_logger("upload")
router = APIRouter(tags=["upload"])


def _detect_type(filename: str, head: bytes) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"不支持的扩展名: {ext}")
    # 魔数校验（docx/doc 起手相同 OLE，细化交给解析器）
    for prefix, kind in ALLOWED_MAGIC_PREFIXES.items():
        if head.startswith(prefix):
            return kind
    if ext in {".txt", ".md"}:
        return "text"
    raise ValidationError("文件魔数不匹配扩展名（疑似伪装）")


@router.post("/upload", response_model=UploadResponse)
async def upload_resume(file: UploadFile = File(...)):
    rid = bind_request_id()
    # 大小校验
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(f"文件超过大小上限 {settings.MAX_UPLOAD_BYTES} 字节")
    if len(data) == 0:
        raise ValidationError("空文件")

    file_type = _detect_type(file.filename or "", data[:8])

    # 落盘（存储 provider）
    storage = get_storage()
    storage_key = uuid.uuid4()
    await storage.save(storage_key, data, file_type)

    # 签发 token（原值仅返回一次，DB 仅存哈希）
    access_token = generate_access_token()

    # 建记录
    async with SessionLocal() as session:
        resume = Resume(
            original_filename=file.filename or "unknown",
            storage_key=storage_key,
            storage_provider=settings.STORAGE_PROVIDER,
            file_type=file_type,
            file_size=len(data),
            status="uploaded",
            access_token_hash=hash_token(access_token),
        )
        session.add(resume)
        await session.flush()
        task = Task(resume_id=resume.id, type="process_resume", status="pending")
        session.add(task)
        await session.commit()
        task_id = task.id
        resume_id = resume.id

    # 投 ARQ 任务
    await enqueue_process_resume(resume_id)

    logger.info("upload_accepted", resume_id=str(resume_id), task_id=str(task_id))
    return UploadResponse(
        task_id=task_id,
        access_token=access_token,
        result_url=f"/result/{task_id}?token={access_token}",
        status="uploaded",
    )
