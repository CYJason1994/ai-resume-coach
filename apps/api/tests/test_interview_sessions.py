"""M3 路由集成测试：鉴权 / owner 校验 / SSE 接线 / 幂等。

不依赖真实 Postgres：monkeypatch `interview_sessions.SessionLocal` 与 `interview_coach.get_llm`，
用 TestClient 跑真实 HTTP（对照 M2 P2-B 同类盲区：原 test_interview_coach 只用 FakeSession，
未覆盖路由鉴权/SSE 帧/owner 校验）。
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.security import generate_access_token, hash_token
from app.main import app
from app.routers import interview_sessions
from app.services import interview_coach


# ── 夹具 ──
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


class _FakeDB:
    def __init__(self, *, resume, job, session, parse=None, questions=None):
        self._resume = resume
        self._job = job
        self._session = session
        self._parse = parse
        self._questions = questions or []
        self.committed = False

    async def get(self, model, id, **kw):
        name = getattr(model, "__name__", "")
        if name == "Resume":
            return self._resume
        if name == "Job":
            return self._job if (self._job and id == self._job.id) else None
        if name == "InterviewSession":
            return self._session
        return None

    async def execute(self, stmt):
        return _Exec(first=self._parse, all_=self._questions)

    def add(self, obj):
        # 真实 Postgres 在 flush() 时由 server default 填充 id；这里模拟该行为
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


class _FakeSessionLocal:
    """`SessionLocal()` 返回自身（async CM），__aenter__ 给出同一 FakeDB。"""

    def __init__(self, db):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _RouterFakeLLM:
    def __init__(self):
        self.calls = 0
        self.last_messages = None

    async def stream_chat(self, messages, *, temperature=0.2, response_format=None):
        self.last_messages = messages
        for c in ["你好，", "请介绍一下", "你自己。"]:
            yield c

    async def chat(self, messages, *, temperature=0.2, response_format=None):
        self.calls += 1
        # 同时覆盖两条真实调用路径：
        # - _score_answer 读 score/strengths/improvements/dimension
        # - overall_evaluate 读 overall_score/summary/top_strengths/top_gaps/suggestion
        return json.dumps(
            {
                "score": 82,
                "overall_score": 82,
                "strengths": ["清晰"],
                "top_strengths": ["表达清晰"],
                "improvements": ["补细节"],
                "top_gaps": ["缺少量化成果"],
                "dimension": "behavioral",
                "summary": "整体表现稳健",
                "suggestion": "补充量化项目成果后更佳",
            }
        )


def _make_session(resume_id, job_id, session_id):
    return SimpleNamespace(
        id=session_id,
        resume_id=resume_id,
        job_id=job_id,
        interview_task_id=None,
        dimension_focus="mixed",
        mode="freeform",
        status="active",
        transcript=[],
        summary_text="",
        overall_score=None,
        updated_at=None,
    )


def _install(monkeypatch, *, token, resume_id, job_id, session_id, job=None, job_none=False, session=None, llm=None):
    resume = SimpleNamespace(id=resume_id, access_token_hash=hash_token(token))
    if job_none:
        effective_job = None
    else:
        effective_job = job or SimpleNamespace(id=job_id, title="Backend", title_zh="后端", required_skills=["python"])
    session = session or _make_session(resume_id, job_id, session_id)
    db = _FakeDB(resume=resume, job=effective_job, session=session)
    monkeypatch.setattr(interview_sessions, "SessionLocal", _FakeSessionLocal(db))
    monkeypatch.setattr(interview_coach, "get_llm", lambda: llm or _RouterFakeLLM())
    return TestClient(app), session, db


def _parse_frames(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        out.append(json.loads(block[len("data:"):].strip()))
    return out


# ── 创建 ──
def test_create_session_success(monkeypatch):
    token = generate_access_token()
    rid, jid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    client, _, _ = _install(monkeypatch, token=token, resume_id=rid, job_id=jid, session_id=sid)
    r = client.post(
        "/api/interview-sessions",
        headers={"X-Access-Token": token},
        json={"resume_id": str(rid), "job_id": str(jid), "dimension_focus": "mixed", "mode": "freeform"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 创建端点由 DB 在 flush 时生成 id（真实 Postgres server default），此处校验为合法非空 uuid
    assert body["session_id"] and body["session_id"] != "None"
    uuid.UUID(body["session_id"])
    assert body["status"] == "active"
    assert body["job_title"] == "后端"


def test_create_session_missing_token(monkeypatch):
    client = TestClient(app)
    r = client.post(
        "/api/interview-sessions",
        json={"resume_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4())},
    )
    assert r.status_code == 403  # require_access_token 在端点前拒绝


def test_create_session_job_not_found(monkeypatch):
    token = generate_access_token()
    rid, jid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # job_none=True → db.get(Job,...) 返回 None → 404
    client, _, _ = _install(monkeypatch, token=token, resume_id=rid, job_id=jid, session_id=sid, job_none=True)
    r = client.post(
        "/api/interview-sessions",
        headers={"X-Access-Token": token},
        json={"resume_id": str(rid), "job_id": str(jid)},
    )
    assert r.status_code == 404, r.text


# ── 流式消息 + owner 校验 ──
def test_message_stream_and_owner_403(monkeypatch):
    token = generate_access_token()
    rid, jid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    llm = _RouterFakeLLM()
    client, session, db = _install(
        monkeypatch, token=token, resume_id=rid, job_id=jid, session_id=sid, llm=llm
    )

    r = client.post(
        f"/api/interview-sessions/{sid}/message",
        headers={"X-Access-Token": token},
        json={"message": "我是张三"},
    )
    assert r.status_code == 200, r.text
    frames = _parse_frames(r.text)
    types = [f["type"] for f in frames]
    assert types.count("token") == 3
    assert "feedback" in types and "done" in types
    assert db.committed is True
    # 用户消息进入 LLM 上下文（回归 M3 审查期修复）
    assert any("我是张三" in m["content"] for m in (llm.last_messages or []))
    assert len(session.transcript) == 2

    # owner token 错误 → 403
    r2 = client.post(
        f"/api/interview-sessions/{sid}/message",
        headers={"X-Access-Token": "wrong-token"},
        json={"message": "x"},
    )
    assert r2.status_code == 403


# ── 结束评估 + 幂等（P2-4）──
def test_finish_idempotent(monkeypatch):
    token = generate_access_token()
    rid, jid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    llm = _RouterFakeLLM()
    client, session, db = _install(
        monkeypatch, token=token, resume_id=rid, job_id=jid, session_id=sid, llm=llm
    )

    # 先发一轮消息（>=2 条 transcript，走真实评估路径而非 P3-3 短路）
    m = client.post(
        f"/api/interview-sessions/{sid}/message",
        headers={"X-Access-Token": token},
        json={"message": "我是张三"},
    )
    assert m.status_code == 200

    r1 = client.post(f"/api/interview-sessions/{sid}/finish", headers={"X-Access-Token": token})
    assert r1.status_code == 200, r1.text
    o1 = r1.json()
    assert o1["overall_score"] == 82
    calls_after_first = llm.calls

    # 第二次幂等：不重复调 LLM（避免双倍成本 / 分数漂移）
    r2 = client.post(f"/api/interview-sessions/{sid}/finish", headers={"X-Access-Token": token})
    assert r2.status_code == 200
    assert llm.calls == calls_after_first
    assert r2.json()["overall_score"] == o1["overall_score"]


def test_finish_short_transcript_no_llm(monkeypatch):
    token = generate_access_token()
    rid, jid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    llm = _RouterFakeLLM()
    client, _, _ = _install(
        monkeypatch, token=token, resume_id=rid, job_id=jid, session_id=sid, llm=llm
    )
    r = client.post(f"/api/interview-sessions/{sid}/finish", headers={"X-Access-Token": token})
    assert r.status_code == 200
    # 内容过短 → P3-3 短路，不调 LLM
    assert llm.calls == 0
    assert r.json()["overall_score"] == 0


# ── 查询 ──
def test_get_session(monkeypatch):
    token = generate_access_token()
    rid, jid, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    client, _, _ = _install(monkeypatch, token=token, resume_id=rid, job_id=jid, session_id=sid)
    r = client.get(f"/api/interview-sessions/{sid}", headers={"X-Access-Token": token})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == str(sid)
