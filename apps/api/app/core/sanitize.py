"""输入/输出消毒（M4 W4）：LLM 文本与用户文本安全处理。

要点：
- 前端（React）默认自动转义，已对 LLM 原始 HTML 做渲染层防护；
  后端此处提供**纵深防御**：在返回/落库前对文本做控制字符清理 + 可选 HTML escape。
- sanitize_llm_text：剥离控制字符（保留 \n \t），可选 html.escape（默认开）。
- escape_html：复用点（供其他序列化路径使用）。
- 仅依赖标准库，无需新增 pip 包。

说明：mock-interview / interview_coach 等 schema 序列化处如需对 LLM 返回文本消毒，
可在响应构造前调用 sanitize_llm_text(text)；本阶段以「提供函数 + 测试」为主，
不强制改动所有返回路径，避免范围蔓延。
"""
from __future__ import annotations

import html
import re

# 控制字符：保留 \n(0x0a) 与 \t(0x09)，其余控制字符（含 \r 之外的）一律剥离。
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_html(text: str) -> str:
    """HTML 转义（含引号），供消毒点复用。"""
    return html.escape(text, quote=True)


def sanitize_llm_text(text: str, *, escape: bool = True) -> str:
    """清洗 LLM/用户文本，返回安全字符串。

    - 剥离控制字符（保留换行/制表）；
    - escape=True 时对 HTML 特殊字符转义，杜绝注入到页面后被执行。
    """
    if text is None:
        return ""
    cleaned = _CTRL_RE.sub("", text)
    if escape:
        cleaned = escape_html(cleaned)
    return cleaned
