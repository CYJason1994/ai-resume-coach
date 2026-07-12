"""M2 面试题目生成：解析容错、规则兜底、LLM 路径（mock）。

不依赖 DB：用 FakeSession 捕获持久化调用，用 FakeLLM 替换 get_llm。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.core.errors import LlmUnavailableError
from app.models.models import Job
from app.schemas.schemas import ResumeStructured
from app.services import interview_gen


# ── 测试夹具 ──
class _Exec:
    rowcount = 0


class FakeSession:
    def __init__(self) -> None:
        self.added: list = []

    async def execute(self, stmt):
        return _Exec()

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


class FakeLLM:
    def __init__(self, text: str | None = None, boom: bool = False) -> None:
        self._text = text
        self._boom = boom

    async def chat(self, messages, *, temperature=0.2, response_format=None):
        if self._boom:
            raise LlmUnavailableError("down")
        return self._text  # type: ignore[return-value]


def _job() -> Job:
    return Job(
        title="Backend Engineer",
        title_zh="后端工程师",
        category="engineering",
        required_skills=["python", "go", "kubernetes"],
        description="Build scalable services.",
    )


def _structured() -> ResumeStructured:
    return ResumeStructured(
        skills=["python", "go"],
        experience_years=5,
        education=["本科"],
        summary="后端开发",
    )


VALID_JSON = json.dumps(
    {
        "questions": [
            {"dimension": "behavioral", "question": "行为题1", "expected_focus": "沟通", "difficulty": "mid"},
            {"dimension": "technical", "question": "技术题1", "expected_focus": "深度", "difficulty": "senior"},
            {"dimension": "role", "question": "岗位题1", "expected_focus": "匹配", "difficulty": "junior"},
            {"dimension": "stress", "question": "压力题1", "expected_focus": "抗压", "difficulty": "mid"},
            {"dimension": "bogus", "question": "非法维度应被过滤", "expected_focus": "x", "difficulty": "mid"},
        ]
    }
)


# ── 解析容错 ──
def test_parse_llm_json_plain():
    items = interview_gen._parse_llm_json(VALID_JSON)
    assert len(items) == 5


def test_parse_llm_json_fenced():
    fenced = "```json\n" + VALID_JSON + "\n```"
    items = interview_gen._parse_llm_json(fenced)
    assert len(items) == 5


def test_parse_llm_json_truncated():
    raw = 'some garbage {"questions": [{"dimension":"role","question":"q"}] } trailing'
    items = interview_gen._parse_llm_json(raw)
    assert items[0]["dimension"] == "role"


def test_parse_llm_json_invalid_raises():
    with pytest.raises(ValueError):
        interview_gen._parse_llm_json("not json at all <<<")


# ── 规则兜底维度覆盖 ──
def test_rule_questions_all_dimensions():
    qs = interview_gen._rule_questions(_structured(), _job())
    dims = {q["dimension"] for q in qs}
    assert dims == set(interview_gen.DIMENSIONS)
    assert all(q["question"] for q in qs)


# ── 生成：LLM 路径 ──
async def test_generate_questions_llm_path(monkeypatch):
    monkeypatch.setattr(interview_gen, "get_llm", lambda: FakeLLM(text=VALID_JSON))
    sess = FakeSession()
    rows, degraded = await interview_gen.generate_questions(
        uuid.uuid4(), _structured(), _job(), sess
    )
    # bogus 维度被过滤，剩 4 条
    assert len(rows) == 4
    assert degraded is False
    dims = {r.dimension for r in rows}
    assert dims == set(interview_gen.DIMENSIONS)
    # 全部写入 session
    assert len(sess.added) == 4
    assert all(isinstance(r, interview_gen.InterviewQuestion) for r in rows)


# ── 生成：无 LLM → 规则兜底 ──
async def test_generate_questions_rule_fallback(monkeypatch):
    monkeypatch.setattr(interview_gen, "get_llm", lambda: FakeLLM(boom=True))
    sess = FakeSession()
    rows, degraded = await interview_gen.generate_questions(
        uuid.uuid4(), _structured(), _job(), sess
    )
    assert degraded is True
    assert rows
    dims = {r.dimension for r in rows}
    assert dims == set(interview_gen.DIMENSIONS)
    # 规则题应包含岗位技能相关题目
    assert any("python" in r.question or "go" in r.question for r in rows)


# ── 生成：LLM 返回坏 JSON → 同样回退规则 ──
async def test_generate_questions_bad_json_fallback(monkeypatch):
    monkeypatch.setattr(interview_gen, "get_llm", lambda: FakeLLM(text="<<<not json>>>"))
    sess = FakeSession()
    rows, degraded = await interview_gen.generate_questions(
        uuid.uuid4(), _structured(), _job(), sess
    )
    assert degraded is True
    assert rows
