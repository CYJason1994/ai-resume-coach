"""结构化抽取（LLM 理解层，与确定性解析解耦）。

- 仅上送白名单字段：raw_text（不附加任何未声明的 PII 元数据）。
- 调 DeepSeek chat + JSON mode → ResumeStructured；pydantic 校验。
- 失败（LLM 不可用 / JSON 非法 / schema 不符）→ 规则提取兜底，绝不阻断链路。
"""
from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.llm import LlmUnavailableError, get_llm
from app.core.logging import get_logger
from app.schemas.schemas import ResumeStructured

logger = get_logger("extractor")
settings = get_settings()

# 上送 LLM 的文本上限（避免超 token / 成本失控）。剩余留给规则兜底与匹配。
LLM_MAX_INPUT_CHARS = 8000

SYSTEM_PROMPT = (
    "你是资深简历解析专家。从用户提供的简历纯文本中抽取结构化字段，"
    "严格按给定 JSON schema 返回，不要任何解释性文字，只返回 JSON 对象。"
    "字段缺失就填空值或空数组，不要编造内容。"
)

_USER_TEMPLATE = "请解析以下简历文本：\n\n{text}"

# 规则兜底的技能词典（中英文常见技能关键词）
_SKILL_HINTS = [
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#",
    "react", "vue", "angular", "node", "nodejs", "django", "flask", "fastapi", "spring",
    "spring boot", "sql", "mysql", "postgresql", "mongodb", "redis", "elasticsearch",
    "docker", "kubernetes", "k8s", "aws", "azure", "gcp", "terraform", "ansible",
    "pytorch", "tensorflow", "pandas", "numpy", "scikit-learn", "机器学习", "深度学习",
    "linux", "git", "ci/cd", "nginx", "kafka", "rabbitmq", "graphql", "protobuf",
    "html", "css", "tailwind", "sass", "webpack", "数据可视化", "etl",
]


def _truncate(text: str) -> str:
    return text[:LLM_MAX_INPUT_CHARS]


async def extract_structured(raw_text: str) -> ResumeStructured:
    text = raw_text.strip()
    if not text:
        return ResumeStructured()
    # 1) 优先 LLM
    try:
        llm = get_llm()
        resp = await llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _USER_TEMPLATE.format(text=_truncate(text))},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp)
        return ResumeStructured(**_coerce(data))
    except (LlmUnavailableError, json.JSONDecodeError, ValidationError) as e:
        logger.warning("extract_llm_fallback", error=str(e), mode="rule")
    # 2) 规则兜底
    return _rule_based(text)


def _coerce(data: dict) -> dict:
    """把 LLM 返回的任意 dict 收敛成 ResumeStructured 可接受的类型。"""
    out: dict = {}
    for key in ResumeStructured.model_fields:
        if key in data:
            out[key] = data[key]
    for list_key in ("skills", "education", "work_history", "projects", "languages"):
        if list_key in out and not isinstance(out[list_key], list):
            out[list_key] = [str(out[list_key])]
    return out


def _rule_based(text: str) -> ResumeStructured:
    """确定性兜底：技能命中词典 + 经验年限正则 + 首行作标题猜测。"""
    lowered = text.lower()
    skills = sorted({s for s in _SKILL_HINTS if s in lowered})
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:年|years?)", lowered)
    exp = int(m.group(1)) if m else None
    first_line = (text.splitlines()[0].strip() if text else None)
    return ResumeStructured(
        title=first_line,
        skills=skills,
        experience_years=exp,
    )
