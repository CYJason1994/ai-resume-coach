"""M0.1 回归：确认 app 各模块可 import（无 SQLAlchemy MappedAnnotationError）。

P0-1 修复验证：models.py 曾因 list 字段缺列类型，在类定义阶段抛
MappedAnnotationError，导致整个 FastAPI 应用无法 import、uvicorn 起不来。
此测试无需数据库即可运行（CI 与本地均可）。
"""
from __future__ import annotations

import importlib


def test_app_imports():
    # 若 models.py 有 list 字段缺类型，import 阶段即抛错
    importlib.import_module("app.models.models")
    importlib.import_module("app.main")
    importlib.import_module("app.workers.worker")
    importlib.import_module("app.routers.jobs")
