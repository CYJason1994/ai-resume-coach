"""合规数据擦除（§7.6 PIPL 删除权 / 被遗忘权）。

删除简历时级联清除其个人数据：
- `resume_parses`（raw_text / structured_data）—— 原始文本与结构化画像
- `job_matches` —— 该简历产生的全部匹配行
硬性删除（而非仅软删），确保原始个人数据不再留存。
"""
from __future__ import annotations

from sqlalchemy import delete

from app.models.models import JobMatch, ResumeParse


async def purge_resume_personal_data(session, resume_id) -> dict:
    """硬删该简历下的解析行与匹配行，返回被删除行数（供审计日志）。"""
    del_matches = await session.execute(
        delete(JobMatch).where(JobMatch.resume_id == resume_id)
    )
    del_parses = await session.execute(
        delete(ResumeParse).where(ResumeParse.resume_id == resume_id)
    )
    return {
        "matches_deleted": del_matches.rowcount,
        "parses_deleted": del_parses.rowcount,
    }
