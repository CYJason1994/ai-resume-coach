"""LLM Provider 抽象 + DeepSeek 客户端 + 健康检查 + 降级状态机（§7.8）。

关键点（v0.3）：
- 模型 ID 配置化（chat / embed）。
- 全局并发信号量（LLM_CONCURRENCY=6）。
- 降级状态机：连续 LLM_FAILURE_THRESHOLD 次失败 → 降级 + 冷却；冷却期节流探测恢复
  （M0.1 修正：原 try_recover 在每次调用入口即清零降级，冷却机制形同虚设）。
- 成本护栏：单份熔断（LLM_COST_CAP_PER_RESUME）+ 日预算（M0/M1 进程内内存计数；
  TODO(M2/M4): 迁 Redis 以支持多副本/多进程共享预算，否则多 worker 部署时护栏互不可见）。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import date, datetime, timezone

import httpx

from app.core.config import get_settings
from app.core.errors import LlmUnavailableError
from app.core.logging import get_logger

logger = get_logger("llm")
settings = get_settings()

# 极简 token 计价（构建时核对 https://platform.deepseek.com/api-docs/pricing）
_APPROX_USD_PER_1K_TOKENS = {
    "chat": 0.00006,   # 出入合计粗估
    "embed": 0.00002,  # deepseek-embedding ~$0.02/1M
}

# P1-6：单份简历/单任务的成本核算键（resume_id / task_id 字符串）。
# 用 ContextVar 透传，避免改动所有 chat/embed 调用签名（否则会破坏既有测试替身）。
# 调用方可显式 `set_llm_cost_key` 包裹一次 LLM 调用；方法亦接受可选 cost_key 参数。
_llm_cost_key_ctx: ContextVar[str | None] = ContextVar("llm_cost_key", default=None)


def set_llm_cost_key(key: str | None):
    """在当前 async 上下文绑定成本核算键（resume_id / task_id）。返回 token 供 reset。"""
    return _llm_cost_key_ctx.set(key)


def reset_llm_cost_key(token) -> None:
    _llm_cost_key_ctx.reset(token)


def _resolve_cost_key(cost_key: str | None) -> str | None:
    return cost_key if cost_key is not None else _llm_cost_key_ctx.get()


class DegradationState:
    """单点 LLM 韧性（R2-C3）。连续失败 → 降级冷却；冷却到期才节流探测，成功才恢复。"""

    def __init__(self, threshold: int, cooldown_s: float = 30.0, probe_backoff_s: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.probe_backoff_s = probe_backoff_s
        self._failures = 0
        self._degraded_until = 0.0
        self._next_probe_at = 0.0

    @property
    def degraded(self) -> bool:
        return time.monotonic() < self._degraded_until

    def note_success(self) -> None:
        self._failures = 0
        self._degraded_until = 0.0
        self._next_probe_at = 0.0

    def note_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._degraded_until = time.monotonic() + self.cooldown_s
            self._next_probe_at = self._degraded_until + self.probe_backoff_s
            logger.warning("llm_degraded", failures=self._failures, cooldown_s=self.cooldown_s)

    async def maybe_recover(self, probe) -> bool:
        """冷却未到期直接返回 False（勿频繁探测 API）；到期则发起一次探测，成功退出降级。"""
        if not self.degraded:
            return True
        now = time.monotonic()
        if now < self._next_probe_at:
            return False
        ok = False
        try:
            ok = bool(await probe())
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self.note_success()
            return True
        self._next_probe_at = now + self.probe_backoff_s
        return False


class LlmProvider:
    def __init__(self) -> None:
        # 无 API KEY 时不含 Authorization header（调用会 401，被降级逻辑正常捕获），
        # 避免 httpx 因 "Bearer "（尾随空格）抛 InvalidHeader。
        headers = (
            {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"}
            if settings.DEEPSEEK_API_KEY
            else {}
        )
        self._client = httpx.AsyncClient(
            base_url=settings.LLM_BASE_URL,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            headers=headers,
        )
        self._sem = asyncio.Semaphore(settings.LLM_CONCURRENCY)
        self.degradation = DegradationState(settings.LLM_FAILURE_THRESHOLD)
        # TODO(M2/M4): 进程内内存计数，多副本部署下各进程预算互不可见；
        # 应迁 Redis（incrbyfloat + TTL）实现共享日预算。
        self._daily_spend: dict[str, float] = {}
        # P1-6：单份简历/单任务累计花费（键为 resume_id / task_id 字符串）。
        self._resume_spend: dict[str, float] = {}
        self._spend_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── 成本护栏 ──
    async def _track_cost(self, kind: str, tokens: int, cost_key: str | None = None) -> None:
        today = date.today().isoformat()
        cost = tokens / 1000 * _APPROX_USD_PER_1K_TOKENS[kind]
        async with self._spend_lock:
            self._daily_spend.setdefault(today, 0.0)
            self._daily_spend[today] += cost
            if cost_key is not None:
                self._resume_spend[cost_key] = self._resume_spend.get(cost_key, 0.0) + cost

    @property
    def daily_spend(self) -> float:
        return self._daily_spend.get(date.today().isoformat(), 0.0)

    def resume_spend(self, cost_key: str) -> float:
        """返回某 resume_id / task_id 累计 LLM 花费（P1-6）。"""
        return self._resume_spend.get(cost_key, 0.0)

    def _budget_ok(self) -> bool:
        return self.daily_spend < settings.LLM_DAILY_BUDGET

    def _resume_budget_ok(self, cost_key: str | None) -> bool:
        """P1-6：单份简历/任务成本护栏（默认 ~$0.01）。无 cost_key 时不限制。"""
        if cost_key is None:
            return True
        return self.resume_spend(cost_key) < settings.LLM_COST_CAP_PER_RESUME

    async def _ensure_available(self, cost_key: str | None = None) -> None:
        """调用前校验预算与降级状态；降级冷却中直接抛错（不清除降级）。

        P1-6：同时校验单份简历/任务成本上限；超额时抛错，
        由调用方的规则兜底接管（不白屏/不崩）。
        """
        if not self._budget_ok():
            raise LlmUnavailableError("LLM 日预算耗尽，使用规则匹配兜底")
        if not self._resume_budget_ok(cost_key):
            raise LlmUnavailableError("单份简历 LLM 成本已达上限，使用规则匹配兜底")
        if self.degradation.degraded:
            recovered = await self.degradation.maybe_recover(self.health_check)
            if not recovered:
                raise LlmUnavailableError("LLM 降级冷却中，使用规则匹配兜底")

    # ── 接口 ──
    async def chat(self, messages: list[dict], *, temperature: float = 0.2,
                   response_format: dict | None = None, cost_key: str | None = None) -> str:
        key = _resolve_cost_key(cost_key)
        await self._ensure_available(key)
        payload: dict = {
            "model": settings.LLM_CHAT_MODEL,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        async with self._sem:
            try:
                r = await self._client.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                usage = data.get("usage", {})
                await self._track_cost("chat", usage.get("total_tokens", 0), key)
                self.degradation.note_success()
                return data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001
                self.degradation.note_failure()
                logger.warning("llm_chat_failed", error=str(e))
                raise LlmUnavailableError(str(e)) from e

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        response_format: dict | None = None,
        cost_key: str | None = None,
    ) -> AsyncIterator[str]:
        """流式对话（SSE 友好）：逐片 yield 内容增量（DeepSeek `stream:true`）。

        复用信号量(6) + 预算护栏 + 降级状态机；失败转 `LlmUnavailableError`
        （降级语义与 `chat` 一致）。仅产出 `choices[0].delta.content` 文本增量。
        """
        key = _resolve_cost_key(cost_key)
        await self._ensure_available(key)
        # DeepSeek（OpenAI 兼容）默认流式不回传 usage；显式开启才能在末片记账，
        # 否则 M3 流式面试的成本不计入日预算护栏（P1-1）。
        payload: dict = {
            "model": settings.LLM_CHAT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # 流式与 json_object 绝大多数 provider 互斥；响应格式仅在非流式时附加（P3-5）
        if response_format and not payload.get("stream"):
            payload["response_format"] = response_format
        async with self._sem:
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        # 末片 usage-only 时 choices 为空列表，需容错（P1-1 修复相关）
                        choices = chunk.get("choices") or [{}]
                        first = choices[0] if choices else {}
                        delta = first.get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content  # type: ignore[misc]
                        # 末片携带 usage，顺手记账（开启 include_usage 后生效）
                        if chunk.get("usage"):
                            await self._track_cost(
                                "chat", chunk["usage"].get("total_tokens", 0), key
                            )
                self.degradation.note_success()
            except Exception as e:  # noqa: BLE001
                self.degradation.note_failure()
                logger.warning("llm_stream_chat_failed", error=str(e))
                raise LlmUnavailableError(str(e)) from e

    async def embed(self, texts: list[str], cost_key: str | None = None) -> list[list[float]]:
        key = _resolve_cost_key(cost_key)
        await self._ensure_available(key)
        async with self._sem:
            try:
                r = await self._client.post(
                    "/embeddings",
                    json={"model": settings.LLM_EMBED_MODEL, "input": texts},
                )
                r.raise_for_status()
                data = r.json()
                await self._track_cost("embed", data.get("usage", {}).get("total_tokens", 0), key)
                self.degradation.note_success()
                return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
            except Exception as e:  # noqa: BLE001
                self.degradation.note_failure()
                logger.warning("llm_embed_failed", error=str(e))
                raise LlmUnavailableError(str(e)) from e

    async def health_check(self) -> bool:
        """探活：调用模型列表端点。"""
        try:
            r = await self._client.get("/models", timeout=10.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False


# 全局单例
_provider: LlmProvider | None = None


def get_llm() -> LlmProvider:
    global _provider
    if _provider is None:
        _provider = LlmProvider()
    return _provider
