"""M2 面试题目生成：解析容错、规则兜底、LLM 路径（mock）、隔离与白名单。

不依赖 DB：用 FakeSession 捕获持久化调用，用 FakeLLM 替换 get_llm。
隔离逻辑（P1-A）用内存 StoreSession 模拟按 task_id 过滤。
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app.core.errors import LlmUnavailableError
from app.models.models import InterviewQuestion, Job
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


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class StoreSession:
    """内存版会话：保存 InterviewQuestion 并支持按 task_id 过滤（模拟 P1-A 隔离）。"""

    def __init__(self) -> None:
        self.added: list[InterviewQuestion] = []

    async def execute(self, stmt):
        return _Exec()

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def scalars(self, stmt):
        # 解析 where(InterviewQuestion.task_id == X)，从内存中过滤
        tid = None
        wc = getattr(stmt, "whereclause", None)
        try:
            if wc is not None and getattr(wc, "left", None) is not None:
                if getattr(wc.left, "key", None) == "task_id":
                    tid = wc.right.value
        except Exception:  # noqa: BLE001
            tid = None
        if tid is None:
            rows = list(self.added)
        else:
            rows = [r for r in self.added if r.task_id == tid]
        rows = sorted(rows, key=lambda r: r.order_index)
        return _Result(rows)


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


# ── 白名单上送（P2-A）：不得含 work_history/projects 等 PII ──
def test_build_messages_whitelist_excludes_pii():
    msgs = interview_gen._build_messages(_structured(), _job())
    user_content = msgs[1]["content"]
    assert "work_history" not in user_content
    assert "projects" not in user_content
    # 白名单字段应存在
    assert "skills" in user_content
    assert "summary" in user_content


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
    tid = uuid.uuid4()
    rows, degraded = await interview_gen.generate_questions(
        task_id=tid, resume_id=uuid.uuid4(), structured=_structured(), job=_job(), session=sess
    )
    # bogus 维度被过滤，剩 4 条
    assert len(rows) == 4
    assert degraded is False
    dims = {r.dimension for r in rows}
    assert dims == set(interview_gen.DIMENSIONS)
    # 全部写入 session 且归属正确 task_id（P1-A）
    assert len(sess.added) == 4
    assert all(isinstance(r, InterviewQuestion) for r in rows)
    assert all(r.task_id == tid for r in rows)


# ── 生成：无 LLM → 规则兜底 ──
async def test_generate_questions_rule_fallback(monkeypatch):
    monkeypatch.setattr(interview_gen, "get_llm", lambda: FakeLLM(boom=True))
    sess = FakeSession()
    rows, degraded = await interview_gen.generate_questions(
        task_id=uuid.uuid4(), resume_id=uuid.uuid4(), structured=_structured(), job=_job(), session=sess
    )
    assert degraded is True
    assert rows
    dims = {r.dimension for r in rows}
    assert dims == set(interview_gen.DIMENSIONS)
    # 规则题应包含岗位技能相关题目
    assert any("python" in r.question or "go" in r.question for r in rows)


# ── 生成：LLM 返回坏 JSON → 同样回退规则（P3-A 任何异常兜底）──
async def test_generate_questions_bad_json_fallback(monkeypatch):
    monkeypatch.setattr(interview_gen, "get_llm", lambda: FakeLLM(text="<<<not json>>>"))
    sess = FakeSession()
    rows, degraded = await interview_gen.generate_questions(
        task_id=uuid.uuid4(), resume_id=uuid.uuid4(), structured=_structured(), job=_job(), session=sess
    )
    assert degraded is True
    assert rows


# ── 隔离查询（P1-A）：同一简历对多岗位生成，GET 按 task_id 精确取回 ──
async def test_get_interview_by_task_isolation():
    store = StoreSession()
    job_a = _job()
    job_b = Job(title="Frontend Engineer", title_zh="前端工程师", required_skills=["react"])
    structured = _structured()
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    rid = uuid.uuid4()

    # 模拟两批生成写入同一 store（实际分别由不同 task 触发）
    rows_a, _ = await interview_gen.generate_questions(
        task_id=tid_a, resume_id=rid, structured=structured, job=job_a, session=store
    )
    rows_b, _ = await interview_gen.generate_questions(
        task_id=tid_b, resume_id=rid, structured=structured, job=job_b, session=store
    )
    # 两批都落库
    assert len(store.added) == len(rows_a) + len(rows_b)

    # 按 task_id 查询应各自隔离，不串味
    got_a = await interview_gen.get_interview_by_task(tid_a, store)
    got_b = await interview_gen.get_interview_by_task(tid_b, store)
    assert {r.task_id for r in got_a} == {tid_a}
    assert {r.task_id for r in got_b} == {tid_b}
    assert len(got_a) == len(rows_a)
    assert len(got_b) == len(rows_b)
    # 岗位标题应来自各自 job
    assert all(r.job_title == "后端工程师" for r in got_a)
    assert all(r.job_title == "前端工程师" for r in got_b)
