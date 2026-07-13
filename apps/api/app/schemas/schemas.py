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


# ── 结构化抽取（M1-2）──
class ResumeStructured(BaseModel):
    """上送给匹配的简历结构化视图（PIPL：不存身份证/手机号等强 PII）。"""

    name: str | None = None
    title: str | None = None  # 当前/目标职位
    summary: str | None = None
    skills: list[str] = []
    experience_years: int | None = None
    education: list[str] = []
    work_history: list[str] = []
    projects: list[str] = []
    languages: list[str] = []
    location: str | None = None


# ── 匹配结果（M1-3）──
class MatchItem(BaseModel):
    job_id: str
    title: str | None = None
    title_zh: str | None = None
    category: str | None = None
    score: float
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    rationale: str | None = None


class ResumeResultResponse(BaseModel):
    task_id: str
    resume_id: str
    status: str
    structured: ResumeStructured | None = None
    matches: list[MatchItem] = []


# ── 面试题目生成（M2，§7.5）──
# 维度英文 key（与 interview_gen.DIMENSIONS 保持一致）
INTERVIEW_DIMENSIONS = ["behavioral", "technical", "role", "stress"]


class InterviewGenerateRequest(BaseModel):
    resume_id: uuid.UUID
    job_id: uuid.UUID


class InterviewQuestionItem(BaseModel):
    id: str
    dimension: str
    question: str
    expected_focus: str | None = None
    difficulty: str | None = None
    order_index: int


class InterviewListResponse(BaseModel):
    task_id: str
    resume_id: str
    job_id: str
    job_title: str
    status: str
    degraded: bool = False  # 是否走规则模板兜底（LLM 不可用）
    error_text: str | None = None  # 失败原因 / 降级提示（status=done 时可能为降级备注）
    questions: list[InterviewQuestionItem] = []


# ── 模拟面试官（M3，§7.5）──
class SessionCreateRequest(BaseModel):
    resume_id: uuid.UUID
    job_id: uuid.UUID
    interview_task_id: uuid.UUID | None = None  # 可选：关联 M2 面试题任务（作为脚本种子）
    dimension_focus: str | None = None  # behavioral|technical|role|stress|mixed
    mode: str | None = None  # freeform | scripted


class SessionMessageRequest(BaseModel):
    message: str


class FeedbackItem(BaseModel):
    score: int
    strengths: list[str] = []
    improvements: list[str] = []
    dimension: str = "mixed"


class ChatMessage(BaseModel):
    role: str
    content: str
    feedback: FeedbackItem | None = None


class SessionView(BaseModel):
    session_id: str
    resume_id: str
    job_id: str
    job_title: str
    dimension_focus: str
    status: str
    transcript: list[ChatMessage] = []
    overall_score: dict | None = None


class SessionOverall(BaseModel):
    session_id: str
    status: str
    overall_score: int | None = None
    summary: str | None = None
    top_strengths: list[str] = []
    top_gaps: list[str] = []
    suggestion: str | None = None
