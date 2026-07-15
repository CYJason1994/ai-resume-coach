"""observability 模块测试（TDD）。

覆盖：
- 无 Sentry DSN 时 setup_observability 不抛、不联网。
- 有 DSN 时 sentry_sdk.init 被按预期参数调用。
- 中间件不破坏 SSE（流式响应原样透传）且响应头含 X-Trace-ID。
- span 被正确创建并带 http.method / http.status_code / http.target 等 attributes。
- record_event 调用不抛。

说明：中间件集成测试使用独立的 FastAPI 实例（避免污染已启动的全局 app，
全局 app 由 `from app.main import app` 导入以验证导入无副作用）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

# 导入全局 app，验证可观测性改动不破坏主应用导入（不会触发 lifespan）
from app.main import app as main_app  # noqa: F401
from app.core.observability import (
    APP_VERSION,
    observability_middleware,
    record_event,
    setup_observability,
    trace_id_ctx,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _stub(**overrides: object) -> SimpleNamespace:
    base = dict(
        APP_NAME="ai-resume-coach",
        ENVIRONMENT="development",
        SENTRY_DSN="",
        SENTRY_TRACES_SAMPLE_RATE=1.0,
        OTEL_EXPORTER_OTLP_ENDPOINT="",
        OTEL_SERVICE_NAME="ai-resume-coach-api",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# 共享的 in-memory exporter，便于断言 span
_EXPORTER = InMemorySpanExporter()


# ── 临时探针路由 ──
async def _ob_sse_probe():
    async def gen():
        for i in range(3):
            yield f"data: chunk{i}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _ob_ping():
    return {"ok": True}


@pytest.fixture(scope="session", autouse=True)
def _install_in_memory_tracer():
    # OTel 禁止重复 set_tracer_provider：main.py 导入时会先调用 setup_observability
    # （真实 app 用 Console/no-op exporter）抢先设置全局 provider。必须先重置
    # set-once 守卫，才能让 in-memory provider 成功安装，供 span 断言使用。
    try:
        from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE

        _TRACER_PROVIDER_SET_ONCE._reset()
    except Exception:  # noqa: BLE001 — 不同 otel 版本位置可能不同
        pass
    setup_observability(_stub(), span_exporter=_EXPORTER, force=True)
    yield
    trace_id_ctx.set(None)


@pytest.fixture
def ob_app():
    """独立 FastAPI 应用：注入 observability 中间件 + 探针路由，避免污染已启动的全局 app。"""
    _EXPORTER.clear()
    app = FastAPI()
    app.add_api_route("/api/_ob_sse", _ob_sse_probe, methods=["GET"])
    app.add_api_route("/api/_ob_ping", _ob_ping, methods=["GET"])
    app.middleware("http")(observability_middleware)
    yield app
    trace_id_ctx.set(None)


# ─────────────────────────────────────────────────────────────────────────────
# Sentry / 初始化
# ─────────────────────────────────────────────────────────────────────────────
def test_setup_no_sentry_no_network(monkeypatch: pytest.MonkeyPatch):
    import sentry_sdk

    calls: list[tuple] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **k: calls.append((a, k)))

    # 无 DSN → 不应调用 sentry_sdk.init，且不抛、不联网
    setup_observability(_stub())
    assert calls == []


def test_setup_sentry_called_with_params(monkeypatch: pytest.MonkeyPatch):
    import sentry_sdk

    calls: list[dict] = []

    def fake_init(*_a, **k):
        calls.append(k)

    monkeypatch.setattr(sentry_sdk, "init", fake_init)

    settings = _stub(SENTRY_DSN="https://abc@sentry.example/42")
    setup_observability(settings)

    assert calls, "SENTRY_DSN 非空时应调用 sentry_sdk.init"
    kw = calls[0]
    assert kw["dsn"] == "https://abc@sentry.example/42"
    assert kw["environment"] == settings.ENVIRONMENT
    assert kw["release"] == f"{settings.APP_NAME}@{APP_VERSION}"
    assert kw["traces_sample_rate"] == settings.SENTRY_TRACES_SAMPLE_RATE


def test_setup_with_real_settings_is_silent():
    # 真实 Settings（尚未合并新字段）也应静默、不抛
    from app.core.config import get_settings

    setup_observability(get_settings())
    assert True


def test_main_app_imports_without_side_effects():
    # 导入主应用不应因可观测性改动而抛错（无 Sentry / 无 OTLP 时静默）
    assert main_app is not None


# ─────────────────────────────────────────────────────────────────────────────
# 中间件：SSE 透传 + X-Trace-ID
# ─────────────────────────────────────────────────────────────────────────────
def test_middleware_preserves_sse_and_adds_trace_id(ob_app: FastAPI):
    with TestClient(ob_app) as client:
        with client.stream("GET", "/api/_ob_sse") as r:
            body = r.read().decode()

    # SSE 完整透传，中间件未消费/缓冲 body
    assert "chunk0" in body and "chunk1" in body and "chunk2" in body
    # 响应头注入 trace id
    assert "X-Trace-ID" in r.headers
    assert len(r.headers["X-Trace-ID"]) == 32

    # span 被创建且带正确属性
    spans = _EXPORTER.get_finished_spans()
    assert spans, "期望至少创建一个 span"
    attrs = spans[0].attributes
    assert attrs.get("http.method") == "GET"
    assert attrs.get("http.status_code") == 200
    assert attrs.get("http.target") == "/api/_ob_sse"
    assert attrs.get("trace_id") == r.headers["X-Trace-ID"]


# ─────────────────────────────────────────────────────────────────────────────
# 中间件：普通请求 span 属性
# ─────────────────────────────────────────────────────────────────────────────
def test_span_attributes_for_normal_request(ob_app: FastAPI):
    with TestClient(ob_app) as client:
        r = client.get("/api/_ob_ping")
    assert r.status_code == 200

    spans = _EXPORTER.get_finished_spans()
    assert spans
    attrs = spans[0].attributes
    assert attrs.get("http.method") == "GET"
    assert attrs.get("http.status_code") == 200
    assert attrs.get("http.target") == "/api/_ob_ping"


# ─────────────────────────────────────────────────────────────────────────────
# 业务事件埋点
# ─────────────────────────────────────────────────────────────────────────────
def test_record_event_does_not_raise():
    record_event("llm.call", model="deepseek-v4-flash", tokens=128)
    record_event("quota.denied", subject="ip:1.2.3.4")
    record_event("degradation", reason="llm_unavailable")
    assert True


def test_record_event_correlates_trace_id():
    trace_id_ctx.set("0" * 32)
    # 不抛即可；trace_id 通过日志 struct 关联（此处仅验证不抛）
    record_event("test.event", value=1)
    assert True
