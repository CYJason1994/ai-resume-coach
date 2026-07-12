"""Pydantic Schema（边界校验，信任边界之外一律不信任）。"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# ── 上传 ──
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".md"}
ALLOWED_MAGIC_PREFIXES: dict[bytes, str] = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "docx",  # .docx / .zip(OOXML)
    b"\xd0\xcf\x11\xe0": "doc",  # legacy .doc (OLE)
}


class UploadResponse(BaseModel):
    task_id: uuid.UUID
    access_token: str  # 仅返回一次
    result_url: str
    status: str


# ── 任务状态 ──
class TaskStatus(BaseModel):
    task_id: uuid.UUID
    type: str
    status: str
    progress: int
    error_text: str | None = None
    updated_at: datetime


class ResumeSummary(BaseModel):
    resume_id: uuid.UUID
    original_filename: str
    file_type: str
    status: str


# ── 岗位源摄入 ──
class SeedResponse(BaseModel):
    provider: str
    ingested: int
    skipped: int
    errors: int
    message: str
