"""P1-1 / P1-6 / P1-7 / P2-4 / P2-5 / P2-6 修复验证（新建，不改动既有测试）。

覆盖：
- P1-1：observability 中间件对 call_next 仅调用一次（正常与异常路径均不二次调用）。
- P1-6：单份简历 LLM 成本上限（per-resume cap），超额由 _ensure_available 抛错；
        日预算护栏仍在；成本键可经 ContextVar 透传（不破坏既有调用签名）。
- P1-7：worker 全局日预算耗尽守卫（_worker_budget_exhausted）。
- P2-4：JobMatch 幂等护栏助手（已完成跳过 / upsert 回退到 add 的测试替身路径）。
- P2-5：嵌入文本白名单剔除 work_history / projects（PII）。
- P2-6：僵尸 running 任务回收（超时重置为 pending）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.errors import LlmUnavailableError
from app.core.llm import (
    LlmProvider,
    get_llm,
    reset_llm_cost_key,
    set_llm_cost_key,
)
from app.models.models import JobMatch
from app.schemas.schemas import ResumeStructured
from app.workers import tasks as tasks_mod


# ═════════════════════════════════════════════════════════════════════════
# P1-1：中间件 call_next 仅一次
# ═════════════════════════════════════════════════════════════════════════
def _install_in_memory_tracer():
    try:
        from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE

        _TRACER_PROVIDER_SET_ONCE._reset()
    except Exception:  # noqa: BLE001
        pass
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from app.core.observability import setup_observability

    exporter = InMemorySpanExporter()
    setup_observability(
        SimpleNamespace(
            APP_NAME="ai-resume-coach",
            ENVIRONMENT="development",
            SENTRY_DSN="",
            SENTRY_TRACES_SAMPLE_RATE=1.0,
            OTEL_EXPORTER_OTLP_ENDPOINT="",
            OTEL_SERVICE_NAME="ai-resume-coach-api",
        ),
        span_exporter=exporter,
        force=True,
    )
    return exporter


class _FakeReq:
    def __init__(self):
        self.headers = {}
        self.method = "GET"
        self.url = SimpleNamespace(path="/api/x", scheme="http")
        self.state = SimpleNamespace()


class _FakeResp:
    def __init__(self, status=200):
        self.status_code = status
        self.headers = {}


@pytest.fixture
def tracer_setup():
    return _install_in_memory_tracer()


async def test_middleware_calls_call_next_exactly_once(tracer_setup):
    from app.core.observability import observability_middleware

    calls = []

    async def call_next(req):
        calls.append(1)
        return _FakeResp(200)

    req = _FakeReq()
    resp = await observability_middleware(req, call_next)
    assert len(calls) == 1, "call_next 应仅调用一次（正常路径）"
    assert resp.headers.get("X-Trace-ID"), "响应头应注入 X-Trace-ID"


async def test_middleware_does_not_recall_on_exception(tracer_setup):
    from app.core.observability import observability_middleware

    calls = []

    async def call_next(req):
        calls.append(1)
        raise RuntimeError("downstream 500")

    req = _FakeReq()
    with pytest.raises(RuntimeError):
        await observability_middleware(req, call_next)
    # 关键：异常路径也只调用一次（不得二次投递 / 二次计费 / 二次写库）
    assert len(calls) == 1, "异常时不得二次调用 call_next（P1-1 修复核心）"


# ═════════════════════════════════════════════════════════════════════════
# P1-6：单份简历成本上限 + 日预算护栏 + ContextVar 透传
# ═════════════════════════════════════════════════════════════════════════
def _fake_chat_response(tokens: int = 1000) -> httpx.Response:
    resp = httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": tokens},
        },
    )
    # raise_for_status 需要响应关联一个 request 实例
    resp.request = httpx.Request("POST", "http://test.local/chat")
    return resp


async def _make_provider():
    provider = LlmProvider()

    async def fake_post(path, json=None):
        return _fake_chat_response()

    provider._client.post = fake_post
    return provider


async def test_per_resume_cost_cap_rejects_after_limit():
    provider = await _make_provider()
    ck = "resume-A"
    await provider.chat([{"role": "user", "content": "a"}], cost_key=ck)
    assert provider.resume_spend(ck) > 0, "应累计单份成本"

    # 模拟累计已超单份上限（默认 ~$0.01）
    provider._resume_spend[ck] = 1.0
    with pytest.raises(LlmUnavailableError):
        await provider._ensure_available(ck)
    # chat 调用也应因超额抛错，且不实际发起请求
    with pytest.raises(LlmUnavailableError):
        await provider.chat([{"role": "user", "content": "a"}], cost_key=ck)


async def test_daily_budget_guard_still_active():
    provider = await _make_provider()
    from datetime import date

    provider._daily_spend[date.today().isoformat()] = 999.0
    with pytest.raises(LlmUnavailableError):
        await provider._ensure_available()  # 无 cost_key：仅校验日预算


async def test_cost_key_via_contextvar_tracked():
    """成本键可经 ContextVar 透传（保持既有调用签名不变，避免破坏测试替身）。"""
    provider = await _make_provider()
    ck = "ctx-key"
    tok = set_llm_cost_key(ck)
    try:
        await provider.chat([{"role": "user", "content": "a"}])
    finally:
        reset_llm_cost_key(tok)
    assert provider.resume_spend(ck) > 0, "经 ContextVar 透传的成本键应被累计"


# ═════════════════════════════════════════════════════════════════════════
# P1-7：worker 全局日预算耗尽守卫
# ═════════════════════════════════════════════════════════════════════════
def test_worker_budget_exhausted_guard(monkeypatch):
    fake = SimpleNamespace(daily_spend=999.0)
    monkeypatch.setattr(tasks_mod, "get_llm", lambda: fake)
    assert tasks_mod._worker_budget_exhausted() is True
    fake.daily_spend = 0.0
    assert tasks_mod._worker_budget_exhausted() is False


# ═════════════════════════════════════════════════════════════════════════
# P2-4：幂等护栏（已完成跳过 + upsert 回退测试替身路径）
# ═════════════════════════════════════════════════════════════════════════
def test_finished_task_is_skipped():
    done = SimpleNamespace(status="done")
    pending = SimpleNamespace(status="pending")
    running = SimpleNamespace(status="running")
    assert tasks_mod._task_is_finished(done) is True
    assert tasks_mod._task_is_finished(pending) is False
    assert tasks_mod._task_is_finished(running) is False


class _AddOnlySession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


async def test_persist_matches_falls_back_to_add():
    """测试替身无 execute → 回退普通 add，保持旧行为（且不崩溃）。"""
    from app.services.matcher import _persist_matches

    sess = _AddOnlySession()
    m = JobMatch(resume_id=uuid.uuid4(), job_id=uuid.uuid4(), score=50.0)
    await _persist_matches(sess, [m])
    assert sess.added == [m]


# ═════════════════════════════════════════════════════════════════════════
# P2-5：嵌入文本剔除 PII（work_history / projects）
# ═════════════════════════════════════════════════════════════════════════
def test_embed_text_excludes_pii():
    from app.services.matcher import _resume_embed_text

    s = ResumeStructured(
        title="后端工程师",
        summary="高并发系统",
        skills=["python"],
        work_history=["曾就职 ACME 公司任高级工程师"],
        projects=["内部风控系统重构"],
        education=["本科"],
    )
    text = _resume_embed_text(s)
    assert "ACME" not in text, "work_history（公司名 PII）不得进入向量库"
    assert "内部风控系统重构" not in text, "projects 同样不得进入向量库"
    assert "python" in text
    assert "本科" in text


# ═════════════════════════════════════════════════════════════════════════
# P2-6：僵尸 running 任务回收
# ═════════════════════════════════════════════════════════════════════════
def test_stale_running_task_is_reaped():
    old = SimpleNamespace(
        status="running",
        resume_id="r",
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    assert tasks_mod._reap_stale_task(old) is True
    assert old.status == "pending", "僵尸任务应重置为 pending 以便重跑"


def test_fresh_running_task_not_reaped():
    fresh = SimpleNamespace(
        status="running",
        resume_id="r",
        updated_at=datetime.now(timezone.utc),
    )
    assert tasks_mod._reap_stale_task(fresh) is False
    assert fresh.status == "running"


def test_done_task_not_reaped():
    done = SimpleNamespace(
        status="done",
        resume_id="r",
        updated_at=datetime.now(timezone.utc) - timedelta(hours=5),
    )
    assert tasks_mod._reap_stale_task(done) is False
