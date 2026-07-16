"""安全修复验证（P0-1 / P2-3 / P2-15 / P1-8）。

仅覆盖本轮修复点；不修改任何既有测试文件。运行方式见任务说明。
"""
from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.core.quota import client_host_from_request
from app.main import app


# ───────────── P0-1：生产缺失 AUTH_JWT_SECRET 必须 fail-loud ─────────────

def test_auth_dev_fallback_still_roundtrips():
    """非生产且未设置密钥：不抛错，dev 默认可用，会话令牌可往返。"""
    from app.core import auth
    import uuid

    uid = uuid.uuid4()
    tok = auth.create_session_token(uid)
    assert auth.decode_session_token(tok) == uid
    assert auth.decode_session_token("garbage") is None


def test_auth_production_without_secret_raises(monkeypatch):
    """生产环境且未设置 AUTH_JWT_SECRET：模块重载必须抛 RuntimeError。"""
    from app.core import config, auth

    class _ProdNoSecret:
        ENVIRONMENT = "production"
        AUTH_JWT_SECRET = ""
        AUTH_JWT_TTL_SECONDS = 604800

        @property
        def is_production(self) -> bool:
            return True

    monkeypatch.setattr(config, "get_settings", lambda: _ProdNoSecret())
    with pytest.raises(RuntimeError):
        importlib.reload(auth)
    # 还原全局模块状态，避免污染其他用例
    monkeypatch.undo()
    get_settings.cache_clear()
    importlib.reload(auth)


# ───────────── P2-3：upload 的 result_url 不得携带 token ─────────────

def test_upload_result_url_has_no_token(monkeypatch):
    """上传成功后 result_url 仅含 task_id；token 仅经 access_token 字段返回。"""

    class _FakeStorage:
        async def save(self, *a, **k):
            return None

    class _FakeSession:
        def __init__(self):
            self._added = []

        def add(self, obj):
            self._added.append(obj)

        def _assign_ids(self):
            import uuid as _uuid

            for obj in self._added:
                if getattr(obj, "id", None) is None:
                    obj.id = _uuid.uuid4()

        async def flush(self):
            self._assign_ids()

        async def commit(self):
            self._assign_ids()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeSessionLocal:
        def __call__(self):
            return _FakeSession()

    monkeypatch.setattr("app.routers.upload.get_storage", lambda: _FakeStorage())

    async def _noop_enqueue(*a, **k):
        return None

    monkeypatch.setattr("app.routers.upload.enqueue_process_resume", _noop_enqueue)
    monkeypatch.setattr("app.routers.upload.SessionLocal", _FakeSessionLocal())

    client = TestClient(app)
    r = client.post(
        "/api/upload",
        files={"file": ("resume.txt", b"hello world resume text", "text/plain")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token=" not in body["result_url"], body["result_url"]
    assert body["result_url"] == f"/result/{body['task_id']}"
    assert body["access_token"]  # token 仍单独返回


# ───────────── P2-15：全局配额天花板 + XFF 信任 ─────────────

class _FakeGlobal:
    def __init__(self, limit: int):
        self.limit = limit
        self.n = 0

    async def allow_global(self) -> bool:
        self.n += 1
        return self.n <= self.limit


def test_global_quota_rejects_after_limit(monkeypatch):
    """全局天花板先于 per-subject：超过全局上限即返回 429。"""
    fake = _FakeGlobal(3)
    monkeypatch.setattr("app.main.get_global_enforcer", lambda: fake)
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/api/jobs").status_code != 429
    r = client.get("/api/jobs")
    assert r.status_code == 429, r.text
    assert r.json()["title"] == "QUOTA_EXCEEDED"


def _req(host, xff=None):
    class _Client:
        pass

    _Client.host = host
    return type(
        "_Req",
        (),
        {
            "client": _Client() if host is not None else None,
            "headers": {"x-forwarded-for": xff} if xff else {},
        },
    )()


def test_client_host_ignores_xff_when_no_proxy(monkeypatch):
    monkeypatch.setattr("app.core.quota._TRUST_PROXY", False)
    assert client_host_from_request(_req("10.0.0.1", "203.0.113.5, 10.0.0.1")) == "10.0.0.1"


def test_client_host_trusts_xff_when_proxy(monkeypatch):
    monkeypatch.setattr("app.core.quota._TRUST_PROXY", True)
    assert (
        client_host_from_request(_req("10.0.0.1", "203.0.113.5, 10.0.0.1")) == "203.0.113.5"
    )


def test_client_host_falls_back_when_no_client(monkeypatch):
    monkeypatch.setattr("app.core.quota._TRUST_PROXY", False)
    assert client_host_from_request(_req(None, "203.0.113.5")) is None
    monkeypatch.setattr("app.core.quota._TRUST_PROXY", True)
    assert client_host_from_request(_req(None, "203.0.113.5")) == "203.0.113.5"
