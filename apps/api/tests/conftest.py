"""pytest 配置：在 app 模块导入前设置仓库相关的绝对路径，保证测试可复现。

必须在任何 `from app... import` 之前设置，否则 get_settings() 的 lru_cache 会锁定默认相对路径。
"""
from __future__ import annotations

import os
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[3]  # apps/api/tests -> repo root

os.environ.setdefault("CURATED_JOBS_DIR", str(_REPO / "data" / "jobs" / "curated"))
os.environ.setdefault("ZH_OVERLAY_PATH", str(_REPO / "data" / "jobs" / "zh_overlay.json"))
os.environ.setdefault("ONET_SNAPSHOT_PATH", str(_REPO / "data" / "jobs" / "onet" / "snapshot.json"))
