"""ARQ 后台任务（M0 起唯一异步方案，禁用 BackgroundTasks）。

M1：process_resume_task 串联 解析 → 结构化(白名单上送) → 匹配(向量+LLM, 降级)。
cleanup_expired：合规 TTL 清理（定时 cron）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.models import InterviewQuestion, JobMatch, Job, Resume, ResumeParse, Task
from app.services.compliance import purge_resume_personal_data
from app.schemas.schemas import ResumeStructured
from app.core.db import SessionLocal
from app.services.extractor import extract_structured
from app.services.interview_gen import generate_questions
from app.services.matcher import match_resume
from app.services.parser import ParseError, ParserFactory
from app.services.storage import get_storage

settings = get_settings()
logger = get_logger("worker.tasks")

_redis = None


async def _redis_pool():
    global _redis
    if _redis is None:
        _redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _redis


async def enqueue_process_resume(resume_id: uuid.UUID) -> None:
    redis = await _redis_pool()
    await redis.enqueue_job("process_resume_task", str(resume_id))


async def enqueue_seed_jobs() -> None:
    redis = await _redis_pool()
    await redis.enqueue_job("seed_jobs_task")


async def enqueue_generate_interview(
    task_id: uuid.UUID, resume_id: uuid.UUID, job_id: uuid.UUID
) -> None:
    redis = await _redis_pool()
    await redis.enqueue_job(
        "generate_interview_task", str(task_id), str(resume_id), str(job_id)
    )


async def process_resume_task(ctx: dict, resume_id: str) -> None:
    """核心链路：解析 → 结构化 → 匹配。各步失败隔离，不拖垮整条链路。"""
    logger.info("process_start", resume_id=resume_id)
    rid = uuid.UUID(resume_id)
    async with SessionLocal() as session:
        task = await session.scalar(
            select(Task).where(
                Task.resume_id == rid, Task.type == "process_resume"
            )
        )
        if not task:
            logger.warning("process_no_task", resume_id=resume_id)
            return
        resume = await session.get(Resume, rid)
        if not resume:
            task.status = "failed"
            task.error_text = "简历记录缺失"
            await session.commit()
            return

        task.status = "running"
        task.progress = 5
        await session.commit()

        # 1) 加载文件
        try:
            data = await get_storage().load(resume.storage_key, resume.file_type)
        except Exception as e:  # noqa: BLE001
            task.status = "failed"
            task.error_text = f"文件读取失败: {e}"
            await session.commit()
            return
        task.progress = 15
        await session.commit()

        # 2) 解析（确定性）
        parse = ResumeParse(resume_id=rid, status="running")
        session.add(parse)
        await session.flush()
        try:
            raw_text = await ParserFactory.get(resume.file_type).parse(data)
            parse.raw_text = raw_text
            parse.parser_used = resume.file_type
            parse.status = "done"
        except (ParseError, NotImplementedError) as e:
            parse.status = "failed"
            parse.error_text = str(e)
            task.status = "failed"
            task.error_text = f"解析失败: {e}"
            await session.commit()
            return
        task.progress = 40
        await session.commit()

        # 3) 结构化（LLM，失败回退规则）
        structured = ResumeStructured()
        try:
            structured = await extract_structured(raw_text)
            resume.status = "parsed"
        except Exception as e:  # noqa: BLE001
            logger.warning("extract_failed", error=str(e))
        parse.structured_data = structured.model_dump()
        task.progress = 70
        await session.commit()

        # 4) 匹配（向量粗排 + LLM 精排，失败回退规则）
        try:
            matches = await match_resume(rid, structured, session)
            resume.status = "matched" if matches else "parsed"
            logger.info("match_written", count=len(matches))
        except Exception as e:  # noqa: BLE001
            logger.warning("match_failed", error=str(e))
        task.progress = 95
        await session.commit()

        task.status = "done"
        task.progress = 100
        await session.commit()
    logger.info("process_done", resume_id=resume_id)


async def generate_interview_task(
    ctx: dict, task_id: str, resume_id: str, job_id: str
) -> None:
    """M2：生成分维度面试题（加载解析 + 岗位 → interview_gen）。"""
    logger.info("interview_start", task_id=task_id, resume_id=resume_id, job_id=job_id)
    tid, rid, jid = uuid.UUID(task_id), uuid.UUID(resume_id), uuid.UUID(job_id)
    async with SessionLocal() as session:
        task = await session.get(Task, tid)
        if not task:
            logger.warning("interview_no_task", task_id=task_id)
            return
        resume = await session.get(Resume, rid)
        if not resume or resume.deleted_at is not None:
            task.status = "failed"
            task.error_text = "简历记录缺失或已删除"
            await session.commit()
            return
        job = await session.get(Job, jid)
        if not job:
            task.status = "failed"
            task.error_text = "目标岗位不存在"
            await session.commit()
            return

        # 最新解析
        parse = (
            await session.scalars(
                select(ResumeParse)
                .where(ResumeParse.resume_id == rid)
                .order_by(ResumeParse.created_at.desc())
            )
        ).first()
        if not parse or not parse.structured_data:
            task.status = "failed"
            task.error_text = "尚无可用的简历结构化结果，请先完成解析"
            await session.commit()
            return
        try:
            structured = ResumeStructured(**(parse.structured_data or {}))
        except Exception as e:  # noqa: BLE001
            task.status = "failed"
            task.error_text = f"结构化数据异常: {e}"
            await session.commit()
            return

        task.status = "running"
        task.progress = 30
        await session.commit()

        try:
            rows, degraded = await generate_questions(
                task_id=tid, resume_id=rid, structured=structured, job=job, session=session
            )
            task.progress = 100
            task.status = "done"
            task.degraded = degraded  # P2-D：显式记录降级，供 GET 直接读取
            # 降级时把"规则模板兜底"作为信息提示写入 error_text（status 仍为 done）
            task.error_text = (
                "AI 生成暂不可用，已使用规则模板兜底" if degraded else None
            )
            logger.info("interview_done", count=len(rows), degraded=degraded)
        except Exception as e:  # noqa: BLE001
            logger.warning("interview_gen_failed", error=str(e))
            task.status = "failed"
            task.error_text = f"面试题生成失败: {e}"
        await session.commit()
    logger.info("interview_task_finished", task_id=task_id)


async def seed_jobs_task(ctx: dict) -> None:
    """摄入岗位源（O*NET / json，可配置）。失败不影响 API，仅记日志。"""
    from app.services.job_source import get_job_source

    logger.info("seed_start")
    try:
        result = await get_job_source().seed()
        logger.info("seed_done", **result)
    except Exception as e:  # noqa: BLE001
        logger.error("seed_failed", error=str(e))


async def cleanup_expired(ctx: dict) -> dict:
    """合规 TTL 清理：删除过期简历文件并软删记录、失效令牌。"""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        expired = list(
            (
                await session.scalars(
                    select(Resume).where(
                        Resume.retention_until < now, Resume.deleted_at.is_(None)
                    )
                )
            ).all()
        )
        count = 0
        for r in expired:
            try:
                await get_storage().delete(r.storage_key, r.file_type)
            except Exception:  # noqa: BLE001
                pass
            await purge_resume_personal_data(session, r.id)
            r.deleted_at = now
            r.access_token_hash = ""  # 失效令牌
            count += 1
        await session.commit()
    logger.info("cleanup_done", expired=count)
    return {"expired": count}
