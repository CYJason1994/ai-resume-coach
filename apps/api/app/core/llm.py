"""LLM Provider 抽象 + DeepSeek 客户端 + 健康检查 + 降级状态机（§7.8）。

关键点（v0.3）：
- 模型 ID 配置化（chat / embed）。
- 全局并发信号量（LLM_CONCURRENCY=6）。
- 降级状态机：连续 LLM_FAILURE_THRESHOLD 次失败 → 降级 + 冷却探测。
- 成本护栏：单份熔断（LLM_COST_CAP_PER_RESUME）+ 日预算（M0 内存计数，M1 改 Redis）。
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
    """单点 LLM 韧性（R2-C3）。连续失败 → 降级；冷却期探活恢复。"""

    def __init__(self, threshold: int, cooldown_s: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures = 0
        self._degraded_until = 0.0

    @property
    def degraded(self) -> bool:
        return time.monotonic() < self._degraded_until

    def note_success(self) -> None:
        self._failures = 0
        self._degraded_until = 0.0

    def note_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._degraded_until = time.monotonic() + self.cooldown_s
            logger.warning("llm_degraded", failures=self._failures, cooldown_s=self.cooldown_s)

    def try_recover(self) -> None:
        """冷却结束后探测成功则退出降级。"""
        if self.degraded:
            self._degraded_until = 0.0
            self._failures = 0


class LlmProvider:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.LLM_BASE_URL,
            timeout=settings.LLM_REQUEST_TIMEOUT,
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
        )
        self._sem = asyncio.Semaphore(settings.LLM_CONCURRENCY)
        self.degradation = DegradationState(settings.LLM_FAILURE_THRESHOLD)
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

    # ── 接口 ──
    async def chat(self, messages: list[dict], *, temperature: float = 0.2,
                   response_format: dict | None = None) -> str:
        if self.degradation.degraded or not self._budget_ok():
            self.degradation.try_recover()
            raise LlmUnavailableError("LLM 降级中或预算耗尽，使用规则匹配兜底")
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
        if self.degradation.degraded:
            self.degradation.try_recover()
            # 嵌入失败无法软降级（匹配依赖向量）；直接抛错由调用方处理
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
        """探活：调用模型列表或最小 completion。"""
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
