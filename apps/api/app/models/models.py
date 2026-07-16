"""SQLAlchemy 数据模型（PostgreSQL + pgvector）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.core.db import Base

settings = get_settings()
RETENTION_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[uuid.UUID] = mapped_column(Uuid, default=_uuid, unique=True)
    storage_provider: Mapped[str] = mapped_column(String(32), default="local")
    file_type: Mapped[str] = mapped_column(String(16))
    file_size: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    access_token_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 十六进制
    retention_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: _now() + timedelta(days=RETENTION_DAYS)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tasks: Mapped[list["Task"]] = relationship(back_populates="resume")
    parses: Mapped[list["ResumeParse"]] = relationship(back_populates="resume")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M2：是否走规则模板兜底（LLM 不可用时）；显式存储，避免依赖 error_text 子串推断
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    # M2：扩展元数据（如面试任务的 job_id），JSONB 便于演进
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    resume: Mapped["Resume"] = relationship(back_populates="tasks")


class ResumeParse(Base):
    __tablename__ = "resume_parses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parser_used: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    resume: Mapped["Resume"] = relationship(back_populates="parses")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_code: Mapped[str] = mapped_column(String(32), default="curated")
    source_license: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(255))
    title_zh: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    required_skills_zh: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)
    soc_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.LLM_EMBED_DIM), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JobMatch(Base):
    __tablename__ = "job_matches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # P2-4：幂等护栏，配合 matcher._persist_matches 的 upsert 抵抗 ARQ 重复投递
    __table_args__ = (
        UniqueConstraint("resume_id", "job_id", name="uq_job_match_resume_job"),
    )


class InterviewQuestion(Base):
    """M2 面试题目（分维度）。题目按 (resume_id, job_id) 生成，并归属到单次生成任务。

    查询隔离以 task_id 为准（修复 P1-A 跨岗位串味：同一简历对多岗位生成时，
    GET 按 task_id 精确取回本次任务产生的题目，不再按 resume_id 整份混读）。
    """

    __tablename__ = "interview_questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # 归属生成任务（隔离键）
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    job_title: Mapped[str] = mapped_column(String(255))  # 岗位标题快照（展示用）
    dimension: Mapped[str] = mapped_column(String(32))  # behavioral|technical|role|stress
    question: Mapped[str] = mapped_column(Text)
    expected_focus: Mapped[str | None] = mapped_column(Text, nullable=True)  # 考察点
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)  # junior|mid|senior
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InterviewSession(Base):
    """M3 模拟面试官会话：流式对话的转录与评分持久化（token 门控，PIPL 可擦除）。

    - transcript：对话转录（[{role, content, feedback?}]），feedback 归属用户本轮回答。
    - summary_text：周期压缩摘要（上下文管理，防溢出）。
    - overall_score：结束时整体评估（JSONB）。
    - interview_task_id：可选关联 M2 面试题任务（作为访谈脚本种子）。
    """

    __tablename__ = "interview_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    resume_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resumes.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    interview_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dimension_focus: Mapped[str] = mapped_column(String(32), default="mixed")
    mode: Mapped[str] = mapped_column(String(32), default="freeform")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | finished
    transcript: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    overall_score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class User(Base):
    """M4 W1 注册用户（完整账号体系）。

    - 与既有匿名流（resumes.user_id 可空）并存，不破坏 M0-M3 端点；
    - password_hash 用 argon2id（见 app.core.auth）；
    - 会话以 JWT 写入 httpOnly SameSite cookie（BFF/同源代理友好）。
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
