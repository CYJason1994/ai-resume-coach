import logging
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.audit import (
    anonymized_subject,
    audit_event,
    user_subject,
)
from app.core.sanitize import escape_html, sanitize_llm_text
from app.core.scan import ScanResult, scan_bytes
from app.core.security import generate_access_token, hash_token, verify_token
from app.core.security_headers import security_headers_middleware


def test_token_roundtrip():
    tok = generate_access_token()
    assert len(tok) >= 32
    h = hash_token(tok)
    assert verify_token(tok, h) is True
    assert verify_token("wrong", h) is False
    assert verify_token(None, h) is False
    assert verify_token(tok, None) is False


def test_hash_is_deterministic():
    tok = generate_access_token()
    assert hash_token(tok) == hash_token(tok)


# ───────────────────────── M4 W4: 安全审计 ─────────────────────────


def test_audit_event_writes_structured_log(caplog):
    """审计事件含 event=audit，且不泄露 PII / token 原值。"""
    token = "supersecret-raw-access-token-VALUE-1234567890"
    with caplog.at_level(logging.INFO, logger="audit"):
        audit_event(
            "resume.upload",
            subject=anonymized_subject(token),  # 已哈希前缀
            resource="resume",
            resource_id="res-123",
            detail={"email": "victim@example.com", "ip": "203.0.113.5", "size": 1024},
            request_id="req-abc",
        )
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    # 结构化字段
    assert rec.struct["event"] == "audit"
    assert rec.struct["action"] == "resume.upload"
    assert rec.struct["subject"].startswith("anon:token:")
    assert rec.struct["resource"] == "resume"
    assert rec.struct["resource_id"] == "res-123"
    assert rec.struct["request_id"] == "req-abc"
    # PII 被脱敏
    assert rec.struct["detail"]["email"] == "***REDACTED***"
    assert rec.struct["detail"]["ip"] == "***REDACTED***"
    assert rec.struct["detail"]["size"] == 1024
    # 渲染后的日志文本绝不包含原值 / PII 明文
    rendered = caplog.text
    assert token not in rendered
    assert "victim@example.com" not in rendered
    assert "203.0.113.5" not in rendered


def test_audit_event_redacts_raw_subject(caplog):
    """误传原始 token 作为 subject 时，自动哈希前缀，绝不落明文。"""
    raw = "abcdef1234567890RAWTOKEN-never-log-this"
    with caplog.at_level(logging.INFO, logger="audit"):
        audit_event("auth.login", subject=raw)
    rec = caplog.records[0]
    assert rec.struct["subject"].startswith("subj:")
    assert raw not in caplog.text


def test_audit_event_known_subject_prefix_preserved(caplog):
    """已脱敏的类型前缀 subject 原样保留。"""
    with caplog.at_level(logging.INFO, logger="audit"):
        audit_event("view", subject=user_subject("9f8e7d6c-5b4a-4321-...rest"), resource="job")
    assert caplog.records[0].struct["subject"].startswith("user:")


# ───────────────────────── M4 W4: 安全响应头 ─────────────────────────


def test_security_headers_middleware_adds_expected_headers():
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    app.middleware("http")(security_headers_middleware)

    client = TestClient(app)
    resp = client.get("/ping")

    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in resp.headers["Permissions-Policy"]
    # CSP：本阶段默认 Report-Only（不阻断），但头必须存在
    csp = resp.headers.get("Content-Security-Policy") or resp.headers.get(
        "Content-Security-Policy-Report-Only"
    )
    assert csp is not None
    assert "default-src 'self'" in csp


# ───────────────────────── M4 W4: 内容扫描 ─────────────────────────


async def test_scan_bytes_clean_text_is_safe():
    result = await scan_bytes(b"Hello, this is a plain resume text with normal words.")
    assert isinstance(result, ScanResult)
    assert result.safe is True


async def test_scan_bytes_rejects_high_entropy_encrypted():
    # 高熵且无已知 magic → 疑似加密/不可信 payload
    payload = os.urandom(64 * 1024)
    result = await scan_bytes(payload)
    assert result.safe is False
    assert result.reason in {"high_entropy_encrypted"}


async def test_scan_bytes_rejects_zip_bomb():
    import io
    import zipfile

    # 1MB 全零：压缩后极小，解压比远超 100x
    payload = b"\x00" * (1024 * 1024)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", payload)
    bomb = buf.getvalue()

    result = await scan_bytes(bomb)
    assert result.safe is False
    assert result.reason in {"zip_bomb_ratio", "zip_bomb_size"}


# ───────────────────────── M4 W4: 文本消毒 ─────────────────────────


def test_sanitize_llm_text_escapes_html():
    dirty = "<script>alert(1)</script>"
    assert sanitize_llm_text(dirty) == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_sanitize_llm_text_strips_control_chars():
    text = "a\x00b\x07c\n\td"
    assert sanitize_llm_text(text) == "abc\n\td"


def test_sanitize_llm_text_preserves_newline_tab():
    text = "line1\nline2\tend"
    assert sanitize_llm_text(text) == "line1\nline2\tend"


def test_escape_html_reused():
    assert escape_html("<b>&'</b>") == "&lt;b&gt;&amp;&#x27;&lt;/b&gt;"
