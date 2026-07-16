"""add unique constraint on job_matches(resume_id, job_id) (P2-4)

幂等护栏：配合 matcher 的 upsert，抵抗 ARQ at-least-once 重复投递造成的
重复 JobMatch 行（应用层 upsert 已能在无该约束时工作，约束是第二道防线）。
注：与其他代理的迁移可能存在链冲突（down_revision=0002_users），由主代理统一协调链序。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003_job_match_unique"
down_revision = "0002_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_job_match_resume_job", "job_matches", ["resume_id", "job_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_job_match_resume_job", "job_matches", type_="unique")
