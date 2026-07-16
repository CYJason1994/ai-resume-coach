from __future__ import annotations

import json
import pathlib

import pytest

from app.services.extractor import _rule_based

# 用测试文件位置解析，消除 cwd 依赖（之前用 cwd 相对路径 data/eval/golden.json，
# 仅当 pytest 在 apps/api/ 下运行才通过；现改为相对本文件，任意 cwd 均可）。
_GOLDEN = pathlib.Path(__file__).resolve().parent.parent / "data" / "eval" / "golden.json"


def _load_golden() -> list[dict]:
    with open(_GOLDEN, "r", encoding="utf-8") as f:
        return json.load(f)


GOLDEN_CASES = _load_golden()

# 质量基线阈值（P2-9）：至少 80% 黄金集经规则兜底能命中期望技能 + 经验年限。
MIN_PASS_RATE = 0.8


def _case_pass(case: dict) -> bool:
    out = _rule_based(case["raw_text"])
    skills_ok = all(s in out.skills for s in case["expect_skills_contain"])
    years_ok = (case["expect_experience_years"] is None) or (
        out.experience_years == case["expect_experience_years"]
    )
    return skills_ok and years_ok


@pytest.mark.parametrize("case", GOLDEN_CASES)
def test_golden_rule_extract(case):
    """eval 黄金集：规则兜底至少能命中期望技能 / 经验年限（质量基线）。"""
    out = _rule_based(case["raw_text"])
    for s in case["expect_skills_contain"]:
        assert s in out.skills, f"[{case['id']}] 期望技能 {s} 命中，实际 {out.skills}"
    if case["expect_experience_years"] is not None:
        assert out.experience_years == case["expect_experience_years"], (
            f"[{case['id']}] 期望经验 {case['expect_experience_years']} 年，"
            f"实际 {out.experience_years}"
        )


def test_golden_pass_rate():
    """P2-9：黄金集整体通过率不得低于 MIN_PASS_RATE（80%）。"""
    passed = sum(1 for c in GOLDEN_CASES if _case_pass(c))
    rate = passed / len(GOLDEN_CASES)
    assert rate >= MIN_PASS_RATE, (
        f"黄金集通过率 {rate:.0%}（{passed}/{len(GOLDEN_CASES)}）低于基线 {MIN_PASS_RATE:.0%}"
    )
