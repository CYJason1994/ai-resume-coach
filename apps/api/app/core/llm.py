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
import time
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
        self._spend_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── 成本护栏 ──
    async def _track_cost(self, kind: str, tokens: int) -> None:
        today = date.today().isoformat()
        async with self._spend_lock:
            self._daily_spend.setdefault(today, 0.0)
            self._daily_spend[today] += tokens / 1000 * _APPROX_USD_PER_1K_TOKENS[kind]

    @property
    def daily_spend(self) -> float:
        return self._daily_spend.get(date.today().isoformat(), 0.0)

    def _budget_ok(self) -> bool:
        return self.daily_spend < settings.LLM_DAILY_BUDGET

    async def _ensure_available(self) -> None:
        """调用前校验预算与降级状态；降级冷却中直接抛错（不清除降级）。"""
        if not self._budget_ok():
            raise LlmUnavailableError("LLM 日预算耗尽，使用规则匹配兜底")
        if self.degradation.degraded:
            recovered = await self.degradation.maybe_recover(self.health_check)
            if not recovered:
                raise LlmUnavailableError("LLM 降级冷却中，使用规则匹配兜底")

    # ── 接口 ──
    async def chat(self, messages: list[dict], *, temperature: float = 0.2,
                   response_format: dict | None = None) -> str:
        await self._ensure_available()
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
                await self._track_cost("chat", usage.get("total_tokens", 0))
                self.degradation.note_success()
                return data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001
                self.degradation.note_failure()
                logger.warning("llm_chat_failed", error=str(e))
                raise LlmUnavailableError(str(e)) from e

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await self._ensure_available()
        async with self._sem:
            try:
                r = await self._client.post(
                    "/embeddings",
                    json={"model": settings.LLM_EMBED_MODEL, "input": texts},
                )
                r.raise_for_status()
                data = r.json()
                await self._track_cost("embed", data.get("usage", {}).get("total_tokens", 0))
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
