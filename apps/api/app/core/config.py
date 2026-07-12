"""集中式配置：所有配置来自环境变量，启动时校验（fail-fast）。

包含 v0.3 关键决策：
- 模型 ID 配置化（LLM_CHAT_MODEL / LLM_EMBED_MODEL）
- 成本护栏数字（R2-M2）
- 岗位源可配置（R2-C1）
- 匿名访问控制参数（R2-C2）
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── 应用 ──
    APP_NAME: str = "ai-resume-coach"
    ENVIRONMENT: str = "development"
    API_PORT: int = 3001
    WEB_PORT: int = 3000
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # ── 数据库 / Redis ──
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/resume_coach"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── DeepSeek（模型 ID 配置化）──
    DEEPSEEK_API_KEY: str = ""
    LLM_CHAT_MODEL: str = "deepseek-v4-flash"
    LLM_EMBED_MODEL: str = "deepseek-embedding"
    LLM_EMBED_DIM: int = 1536  # 嵌入向量维度，须与 LLM_EMBED_MODEL 输出维度一致
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_REQUEST_TIMEOUT: float = 60.0
    LLM_MAX_RETRIES: int = 3

    # ── 成本护栏（R2-M2）──
    LLM_COST_CAP_PER_RESUME: float = 0.01
    LLM_CONCURRENCY: int = 6
    LLM_DAILY_BUDGET: float = 5.0
    LLM_FAILURE_THRESHOLD: int = 3

    # ── 匿名访问控制（R2-C2）──
    ACCESS_TOKEN_BYTES: int = 32
    ACCESS_TOKEN_HASH_ALGO: str = "sha256"

    # ── 存储 ──
    STORAGE_PROVIDER: str = "local"  # local | minio | cos
    STORAGE_LOCAL_DIR: str = "data/uploads"
    STORAGE_MINIO_ENDPOINT: str = ""
    STORAGE_MINIO_BUCKET: str = ""
    STORAGE_MINIO_ACCESS_KEY: str = ""
    STORAGE_MINIO_SECRET_KEY: str = ""

    # ── 岗位源（R2-C1，可配置）──
    JOB_SOURCE: str = "onet"  # onet | json
    JOB_SOURCE_ZH_OVERLAY: bool = True
    JOB_SOURCE_LIMIT: int = 120
    JOB_SOURCE_CATEGORIES: list[str] = []
    ONET_SNAPSHOT_PATH: str = "data/jobs/onet/snapshot.json"
    CURATED_JOBS_DIR: str = "data/jobs/curated"
    ZH_OVERLAY_PATH: str = "data/jobs/zh_overlay.json"

    # ── 文件上传 ──
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024

    @field_validator("CORS_ORIGINS", "JOB_SOURCE_CATEGORIES", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """全局单例配置；校验在 pydantic 构造时 fail-fast。"""
    return Settings()
