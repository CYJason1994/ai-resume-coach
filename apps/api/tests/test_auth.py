"""账号鉴权路由集成测试（M4 W1，沿用 router 集成测试范式）。

用 monkeypatch 替换 auth.SessionLocal 为内存假库，覆盖：
注册成功 / 重复邮箱 409 / 邮箱格式 400 / 登录错误密码 401 /
登录成功置 cookie / 无 cookie 访问 me 401 / 带 cookie 访问 me 200 / 登出清 cookie。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from app.main import app
from app.models.models import User
from app.routers import auth


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDB:
    def __init__(self):
        self.users: dict[uuid.UUID, User] = {}

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        self.users[obj.id] = obj

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def get(self, model, ident):
        if model is User:
            return self.users.get(ident)
        return None

    async def execute(self, stmt):
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        m = re.search(r"users\.email = '([^']*)'", compiled)
        if m:
            email = m.group(1)
            for u in self.users.values():
                if u.email == email:
                    return _Result(u)
        return _Result(None)


class _FakeSession:
    def __init__(self, db: _FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


class _FakeSessionLocal:
    def __init__(self, db: _FakeDB):
        self._db = db

    def __call__(self) -> _FakeSession:
        return _FakeSession(self._db)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = _FakeDB()
    monkeypatch.setattr(auth, "SessionLocal", _FakeSessionLocal(db))
    return TestClient(app)


def test_register_success_sets_cookie(client: TestClient):
    r = client.post("/api/auth/register", json={"email": "A@Example.com", "password": "supersecret"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "a@example.com"  # 小写归一化
    assert body["id"]
    assert body["created_at"]
    assert body["last_login_at"] is None
    # 会话 cookie 已设置（httpOnly）
    sc = r.headers.get("set-cookie", "")
    assert "arc_session=" in sc
    assert "HttpOnly" in sc
    assert "samesite=lax" in sc.lower()


def test_register_duplicate_409(client: TestClient):
    client.post("/api/auth/register", json={"email": "dup@example.com", "password": "supersecret"})
    r = client.post("/api/auth/register", json={"email": "dup@example.com", "password": "supersecret"})
    assert r.status_code == 409, r.text


def test_register_invalid_email_400(client: TestClient):
    r = client.post("/api/auth/register", json={"email": "not-an-email", "password": "supersecret"})
    assert r.status_code == 400, r.text


def test_register_short_password_422(client: TestClient):
    r = client.post("/api/auth/register", json={"email": "x@example.com", "password": "short"})
    assert r.status_code == 422, r.text


def test_login_wrong_password_401(client: TestClient):
    client.post("/api/auth/register", json={"email": "login@example.com", "password": "supersecret"})
    r = client.post("/api/auth/login", json={"email": "login@example.com", "password": "wrongpass"})
    assert r.status_code == 401, r.text


def test_login_success_then_me(client: TestClient):
    client.post("/api/auth/register", json={"email": "flow@example.com", "password": "supersecret"})
    # 登录（同 client 实例，cookie 透传）
    r = client.post("/api/auth/login", json={"email": "flow@example.com", "password": "supersecret"})
    assert r.status_code == 200, r.text
    # me 带 cookie
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "flow@example.com"


def test_me_without_cookie_401(client: TestClient):
    fresh = TestClient(app)  # 无 cookie 的全新 client
    r = fresh.get("/api/auth/me")
    assert r.status_code == 401, r.text


def test_logout_clears_cookie(client: TestClient):
    client.post("/api/auth/register", json={"email": "out@example.com", "password": "supersecret"})
    r = client.post("/api/auth/logout")
    assert r.status_code == 200, r.text
    sc = r.headers.get("set-cookie", "")
    # 登出应清除 cookie（过期/空值）
    assert "arc_session=" in sc
    assert "Max-Age=0" in sc or 'arc_session=""' in sc or "expires=" in sc.lower()
