import json

import pytest

from app.core.llm import LlmUnavailableError
from app.schemas.schemas import ResumeStructured
from app.services import extractor


class _FakeLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def chat(self, messages, *, response_format=None, temperature=0.2):
        return json.dumps(self._payload)


async def test_extract_llm_success(monkeypatch):
    payload = {
        "title": "后端工程师",
        "skills": ["python", "go"],
        "experience_years": 5,
        "summary": "高并发系统",
    }
    monkeypatch.setattr(extractor, "get_llm", lambda: _FakeLLM(payload))
    out = await extractor.extract_structured("任何文本")
    assert isinstance(out, ResumeStructured)
    assert out.title == "后端工程师"
    assert "python" in out.skills
    assert out.experience_years == 5


async def test_extract_fallback_on_unavailable(monkeypatch):
    class _Boom:
        async def chat(self, *a, **k):
            raise LlmUnavailableError("down")

    monkeypatch.setattr(extractor, "get_llm", lambda: _Boom())
    out = await extractor.extract_structured("技能 python go kubernetes，3年经验")
    # 规则兜底：技能词典命中 + 经验年限正则
    assert isinstance(out, ResumeStructured)
    assert "python" in out.skills
    assert out.experience_years == 3
