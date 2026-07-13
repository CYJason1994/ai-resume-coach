"""M3 模拟面试官：流式、上下文窗口、逐轮评分、降级、整体评估。

不依赖 DB：用 FakeSession 捕获持久化，用 FakeLLM 替换 get_llm（含 stream_chat）。
上下文/隔离/评分逻辑均在此覆盖。
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import LlmUnavailableError
from app.core.llm import get_llm
from app.services import interview_coach


# ── 测试夹具 ──
class _Exec:
    def __init__(self, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class FakeSession:
    """内存版会话：按模型类返回预设对象；execute 返回预设 parse/脚本。"""

    def __init__(self, resume, job, parse=None, questions=None):
        self._resume = resume
        self._job = job
        self._exec = _Exec(first=parse, all_=questions or [])
        self.committed = False

    async def get(self, model, id, **kw):
        # 服务按真实模型类（Resume/Job）查询；按类名匹配返回预设对象
        name = getattr(model, "__name__", "")
        if name == "Resume":
            return self._resume
        if name == "Job":
            return self._job
        return None

    async def execute(self, stmt):
        return self._exec

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


class FakeLLM:
    def __init__(self, reply_chunks=None, chat_response=None, boom=False):
        self._chunks = reply_chunks or ["你好，", "请介绍一下", "你自己。"]
        self._chat = chat_response or json.dumps(
            {"score": 82, "strengths": ["表达清晰"], "improvements": ["补充细节"], "dimension": "behavioral"}
        )
        self._boom = boom

    async def stream_chat(self, messages, *, temperature=0.2, response_format=None):
        if self._boom:
            raise LlmUnavailableError("down")
        self.last_messages = messages  # 记录上下文，供断言
        for c in self._chunks:
            yield c

    async def chat(self, messages, *, temperature=0.2, response_format=None):
        if self._boom:
            raise LlmUnavailableError("down")
        self.last_messages = messages
        return self._chat


def parse_sse(s: str) -> dict:
    for line in s.split("\n"):
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError("no data: line")


def _resume():
    return SimpleNamespace(id=uuid.uuid4(), access_token_hash="x")


def _job():
    return SimpleNamespace(id=uuid.uuid4(), title="Backend", title_zh="后端", required_skills=["python", "go"])


def _parse():
    return SimpleNamespace(structured_data={"skills": ["python"], "experience_years": 5, "summary": "后端开发"})


def _session(**kw):
    base = dict(
        id=uuid.uuid4(),
        resume_id=None,
        job_id=None,
        interview_task_id=None,
        dimension_focus="mixed",
        transcript=[],
        summary_text="",
        status="active",
        overall_score=None,
        updated_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── 上下文窗口 / 摘要 ──
def test_build_context_window_and_summary():
    job = _job()
    sess = _session(
        dimension_focus="technical",
        summary_text="摘要X",
        transcript=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "a4"},
            {"role": "user", "content": "u5"},
            {"role": "assistant", "content": "a5"},
        ],
    )
    msgs = interview_coach.build_context(sess, job, sess.transcript, "简历摘要", [])
    assert msgs[0]["role"] == "system"
    assert any(m["role"] == "system" and "摘要X" in m["content"] for m in msgs)
    window = [m for m in msgs if m["role"] in ("user", "assistant")]
    assert len(window) == 8  # MAX_WINDOW_TURNS：最近 8 条
    assert window[0]["content"] == "u2"  # 最早两条被裁剪


# ── 流式 + 逐轮评分 ──
async def test_stream_session_reply_events(monkeypatch):
    resume, job, parse = _resume(), _job(), _parse()
    sess = _session(resume_id=resume.id, job_id=job.id)
    db = FakeSession(resume, job, parse=parse)
    fake = FakeLLM()
    monkeypatch.setattr(interview_coach, "get_llm", lambda: fake)

    events = [parse_sse(e) async for e in interview_coach.stream_session_reply(db, sess, "我是张三")]

    types = [e["type"] for e in events]
    assert types.count("token") == 3
    assert any(e["type"] == "feedback" for e in events)
    assert events[-1]["type"] == "done"
    assert db.committed is True

    # 回归：用户本轮回答必须进入发给 LLM 的上下文（修复 sess.transcript 未更新导致的盲区）
    assert any("我是张三" in m["content"] for m in (fake.last_messages or []))

    reply = "".join(e["value"] for e in events if e["type"] == "token")
    assert reply == "你好，请介绍一下你自己。"

    fb = [e["value"] for e in events if e["type"] == "feedback"][0]
    assert fb["score"] == 82

    tr = sess.transcript
    assert len(tr) == 2
    assert tr[0]["role"] == "user" and tr[0]["feedback"]["score"] == 82  # 评分归属用户本轮
    assert tr[1]["role"] == "assistant" and tr[1]["content"] == reply


# ── 无 LLM → error 事件，不写助手消息 ──
async def test_stream_session_reply_degraded(monkeypatch):
    resume, job, parse = _resume(), _job(), _parse()
    sess = _session(resume_id=resume.id, job_id=job.id)
    db = FakeSession(resume, job, parse=parse)
    monkeypatch.setattr(interview_coach, "get_llm", lambda: FakeLLM(boom=True))

    events = [parse_sse(e) async for e in interview_coach.stream_session_reply(db, sess, "hi")]
    assert events[0]["type"] == "error"
    assert db.committed is False
    assert sess.transcript == []  # 错误路径不落库助手消息


# ── 整体评估 ──
async def test_overall_evaluate(monkeypatch):
    resume, job = _resume(), _job()
    sess = _session(
        resume_id=resume.id,
        job_id=job.id,
        transcript=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    )
    db = FakeSession(resume, job)
    resp = json.dumps(
        {
            "overall_score": 88,
            "summary": "总体良好",
            "top_strengths": ["基础扎实"],
            "top_gaps": ["深度不足"],
            "suggestion": "多刷题",
        }
    )
    monkeypatch.setattr(interview_coach, "get_llm", lambda: FakeLLM(chat_response=resp))

    overall = await interview_coach.overall_evaluate(db, sess)
    assert overall["overall_score"] == 88
    assert sess.status == "finished"
    assert db.committed is True


# ── LlmProvider.stream_chat 解析（用假 httpx 客户端）──
class _FakeStreamResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeStreamClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, json=None):
        return _FakeStreamResp(self._lines)


async def test_stream_chat_yields_deltas():
    provider = get_llm()  # 已有/新建单例；无 Key 时预算/降级检查仍通过
    lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        "data: [DONE]",
    ]
    provider._client = _FakeStreamClient(lines)
    chunks = [c async for c in provider.stream_chat([{"role": "user", "content": "hi"}])]
    assert chunks == ["你", "好"]
