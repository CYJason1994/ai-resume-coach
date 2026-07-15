"""可观测性：Sentry + OpenTelemetry 追踪/指标 + 业务事件埋点（M4 W3）。

设计约束：
- 完全优雅：Sentry DSN 与 OTLP endpoint 均为空时，不抛异常、不联网、完全静默。
- 中间件绝不消费 / 缓冲响应体：只读写 headers，SSE 流式响应（/api 下多个 streaming 端点）
  必须原样透传。
- 与 app/core/logging 对齐：把 trace_id 注入日志 struct（通过 contextvar + 结构化字段），
  并与 logging 已绑定的 request_id 关联。
- 所有依赖均懒加载，未安装时静默降级，绝不影响应用启动。
"""
from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from typing import Any

# 版本号：应与 pyproject.toml / app/main.py 保持一致，用作 release 标记
APP_VERSION = "0.3.0"

# 与 logging 的 request_id 对齐：在请求期间保存当前 trace_id，供 record_event 等埋点写入日志
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)

# 模块级全局，由 setup_observability 填充；缺失时中间件与埋点安全降级（no-op）
tracer: Any = None
meter: Any = None
_http_requests_counter: Any = None
_http_duration_hist: Any = None
_event_counter: Any = None


# ─────────────────────────────────────────────────────────────────────────────
# 配置读取（兼容 config.py 尚未合并新字段的场景，全部带默认值，绝不抛）
# ─────────────────────────────────────────────────────────────────────────────
def _cfg(settings: Any, name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _gen_trace_id() -> str:
    """生成 128-bit (32 hex) trace_id。"""
    return uuid.uuid4().hex + uuid.uuid4().hex


# ─────────────────────────────────────────────────────────────────────────────
# Sentry
# ─────────────────────────────────────────────────────────────────────────────
def _init_sentry(settings: Any) -> None:
    dsn = _cfg(settings, "SENTRY_DSN", "")
    if not dsn:
        return  # 无 DSN → 完全静默，不联网
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=_cfg(settings, "ENVIRONMENT", "development"),
            release=f"{_cfg(settings, 'APP_NAME', 'ai-resume-coach')}@{APP_VERSION}",
            traces_sample_rate=float(_cfg(settings, "SENTRY_TRACES_SAMPLE_RATE", 1.0)),
            integrations=[StarletteIntegration(), FastApiIntegration()],
            send_default_pii=False,  # 不记录 PII / token
        )
    except Exception:  # noqa: BLE001 — 观测绝不能影响主流程
        pass


# ─────────────────────────────────────────────────────────────────────────────
# OpenTelemetry tracing + metrics
# ─────────────────────────────────────────────────────────────────────────────
def _init_tracing(
    settings: Any,
    *,
    span_exporter: Any = None,
    meter_provider: Any = None,
    force: bool = False,
) -> None:
    global tracer, meter, _http_requests_counter, _http_duration_hist, _event_counter
    if not force and tracer is not None:
        # 已初始化：保持首次设置的 provider/tracer，避免测试间或热重载相互污染
        return
    try:
        from opentelemetry import trace
        from opentelemetry import metrics as otel_metrics
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except Exception:  # noqa: BLE001 — OTel 未安装时静默降级
        return

    service_name = _cfg(settings, "OTEL_SERVICE_NAME", "ai-resume-coach-api")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": APP_VERSION,
            "deployment.environment": _cfg(settings, "ENVIRONMENT", "development"),
        }
    )

    provider = TracerProvider(resource=resource)

    if span_exporter is not None:
        # 测试注入（InMemorySpanExporter）或显式导出器：同步导出便于断言
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        endpoint = _cfg(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                insecure = not str(endpoint).startswith("https")
                exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception:  # noqa: BLE001 — 导出器不可用则退回控制台
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        elif _cfg(settings, "ENVIRONMENT", "development").lower() != "production":
            # dev 无 endpoint：ConsoleSpanExporter 让 span 在本地可见
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        # production 无 endpoint：不加任何 processor → 完全静默、不联网

    try:
        trace.set_tracer_provider(provider)
    except Exception:  # noqa: BLE001
        pass

    # MeterProvider：仅当显式提供（测试）或配置了 OTLP endpoint 时才启用
    mp = meter_provider
    if mp is None and _cfg(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", ""):
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            mp = MeterProvider(
                resource=resource,
                metric_readers=[
                    PeriodicExportingMetricReader(
                        OTLPMetricExporter(
                            endpoint=_cfg(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                            insecure=not str(
                                _cfg(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
                            ).startswith("https"),
                        )
                    )
                ],
            )
        except Exception:  # noqa: BLE001
            mp = None

    if mp is not None:
        try:
            otel_metrics.set_meter_provider(mp)
        except Exception:  # noqa: BLE001
            pass

    # 直接从本 provider 取 tracer，避免全局 set_tracer_provider 被 set-once 守卫
    # 拒绝后退化到旧 provider（测试/热重载等已 set 场景更稳定）。
    tracer = provider.get_tracer(service_name, APP_VERSION)
    meter = otel_metrics.get_meter(service_name, APP_VERSION)
    _create_instruments()


def _create_instruments() -> None:
    global _http_requests_counter, _http_duration_hist, _event_counter
    try:
        _http_requests_counter = meter.create_counter(
            "http.server.requests",
            description="Total HTTP requests handled by the API",
        )
        _http_duration_hist = meter.create_histogram(
            "http.server.duration",
            unit="s",
            description="HTTP request duration in seconds",
        )
        _event_counter = meter.create_counter(
            "app.events",
            description="Business events (LLM calls, degradations, quota denials, ...)",
        )
    except Exception:  # noqa: BLE001
        _http_requests_counter = _http_duration_hist = _event_counter = None


def setup_observability(
    settings: Any,
    *,
    span_exporter: Any = None,
    meter_provider: Any = None,
    force: bool = False,
) -> None:
    """初始化可观测性。

    无配置（SENTRY_DSN / OTEL_EXPORTER_OTLP_ENDPOINT 均空）时完全静默、不抛、不联网。

    span_exporter / meter_provider 为可选注入点，主要用于测试；生产请勿传入。
    force=True 强制重建（测试注入 in-memory exporter 时需要；幂等：默认已初始化则跳过）。
    """
    _init_sentry(settings)
    _init_tracing(
        settings, span_exporter=span_exporter, meter_provider=meter_provider, force=force
    )


def get_tracer() -> Any:
    return tracer


def get_meter() -> Any:
    return meter


# ─────────────────────────────────────────────────────────────────────────────
# W3C traceparent 解析 / 父上下文
# ─────────────────────────────────────────────────────────────────────────────
def _parse_traceparent(header: str | None):
    """解析 W3C traceparent: version-trace_id-span_id-flags（均为 hex）。"""
    if not header:
        return None
    parts = header.split("-")
    if len(parts) != 4:
        return None
    try:
        version, trace_id, span_id, flags = parts
        tid = int(trace_id, 16)
        sid = int(span_id, 16)
        fl = int(flags, 16)
    except ValueError:
        return None
    if version == "00" and len(trace_id) == 32 and len(span_id) == 16:
        return (tid, sid, fl)
    return None


def _parent_context(parent):
    if not parent:
        return None
    try:
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
            set_span_in_context,
        )

        trace_id, span_id, flags = parent
        return set_span_in_context(
            NonRecordingSpan(
                SpanContext(
                    trace_id=trace_id,
                    span_id=span_id,
                    is_remote=True,
                    trace_flags=TraceFlags(flags),
                )
            )
        )
    except Exception:  # noqa: BLE001
        return None


def _route(request: Any) -> str:
    return getattr(request, "path", None) or request.url.path


def _request_id() -> str | None:
    try:
        from app.core.logging import request_id_ctx as _rid_ctx

        return _rid_ctx.get()
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 中间件（由主 agent 通过 app.middleware("http")(observability_middleware) 注册）
# ─────────────────────────────────────────────────────────────────────────────
async def observability_middleware(request: Any, call_next: Any) -> Any:
    start = time.perf_counter()
    parent = _parse_traceparent(request.headers.get("traceparent"))
    ctx = _parent_context(parent)
    tid = format(parent[0], "032x") if parent else _gen_trace_id()
    # 尽早绑定 trace_id，使请求处理期间的所有日志都能关联
    trace_id_ctx.set(tid)
    request.state.trace_id = tid

    response = None
    if tracer is not None:
        try:
            from opentelemetry.trace import SpanKind

            with tracer.start_as_current_span(
                f"{request.method} {request.url.path}",
                context=ctx,
                kind=SpanKind.SERVER,
            ) as span:
                sc = span.get_span_context()
                if sc.trace_id:
                    tid = format(sc.trace_id, "032x")
                    trace_id_ctx.set(tid)
                    request.state.trace_id = tid
                _set_common_attrs(span, request, tid)
                response = await call_next(request)
                _finalize_span(span, request, response, tid)
        except Exception:  # noqa: BLE001 — 观测失败绝不影响主流程
            if response is None:
                try:
                    response = await call_next(request)
                except Exception:
                    raise
    else:
        response = await call_next(request)

    duration = time.perf_counter() - start
    _record_request_metrics(request, response, duration, tid)
    # 关键：绝不消费响应体，仅写 header（SSE 流式响应原样透传）
    if tid:
        response.headers["X-Trace-ID"] = tid
    return response


def _set_common_attrs(span: Any, request: Any, tid: str) -> None:
    try:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.target", request.url.path)
        span.set_attribute("http.route", _route(request))
        span.set_attribute("http.scheme", request.url.scheme)
        span.set_attribute("trace_id", tid)
        rid = _request_id()
        if rid is not None:
            span.set_attribute("request_id", rid)
    except Exception:  # noqa: BLE001
        pass


def _finalize_span(span: Any, request: Any, response: Any, tid: str) -> None:
    try:
        from opentelemetry.trace import StatusCode

        span.set_attribute("http.status_code", response.status_code)
        span.set_attribute("http.route", _route(request))
        span.set_attribute("trace_id", tid)
        span.set_status(
            StatusCode.ERROR if response.status_code >= 500 else StatusCode.OK
        )
        # 注入 W3C traceparent 到响应头，便于下游服务串联
        sc = span.get_span_context()
        span_id_hex = format(sc.span_id, "016x") if sc.span_id else "0" * 16
        response.headers["traceparent"] = f"00-{tid}-{span_id_hex}-01"
    except Exception:  # noqa: BLE001
        pass


def _record_request_metrics(request: Any, response: Any, duration: float, tid: str) -> None:
    try:
        if _http_requests_counter is not None:
            _http_requests_counter.add(
                1,
                {
                    "method": request.method,
                    "status": str(response.status_code),
                    "route": _route(request),
                },
            )
        if _http_duration_hist is not None:
            _http_duration_hist.record(
                duration,
                {"method": request.method, "route": _route(request)},
            )
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 业务事件埋点
# ─────────────────────────────────────────────────────────────────────────────
def _log_safe(attrs: dict) -> dict:
    out: dict = {}
    for k, v in attrs.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def record_event(name: str, **attrs: Any) -> None:
    """业务事件埋点（LLM 调用次数、降级触发、配额拒绝等）。失败静默。

    内部以 meter 计数 + 结构化日志（携带 trace_id）双重记录。
    """
    tid = trace_id_ctx.get()
    try:
        if _event_counter is not None:
            _event_counter.add(1, {"event": name})
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.core.logging import get_logger

        get_logger("observability").info(
            "event",
            event=name,
            trace_id=tid,
            **_log_safe(attrs),
        )
    except Exception:  # noqa: BLE001
        pass
