from __future__ import annotations

import json
import pathlib

import pytest

from app.services.extractor import _rule_based

# 用测试文件位置解析，消除 cwd 依赖（之前用 cwd 相对路径 data/eval/golden.json，
# 仅当 pytest 在 apps/api/ 下运行才通过；现改为相对本文件，任意 cwd 均可）。
_GOLDEN = pathlib.Path(__file__).resolve().parent.parent / "data" / "eval" / "golden.json"


@pytest.fixture(scope="module")
def cases():
    with open(_GOLDEN, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("idx", range(4))
def test_golden_rule_extract(cases, idx):
    """eval 黄金集：规则兜底至少能命中期望技能 / 经验年限（质量基线）。"""
    case = cases[idx]
    out = _rule_based(case["raw_text"])
    for s in case["expect_skills_contain"]:
        assert s in out.skills, f"期望技能 {s} 命中，实际 {out.skills}"
    if case["expect_experience_years"] is not None:
        assert out.experience_years == case["expect_experience_years"]
