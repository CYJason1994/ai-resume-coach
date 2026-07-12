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
