"""O*NET 30.3 → snapshot.json 转换（CC BY 4.0，美国劳工部）。

将 O*NET 文本表映射为 `JobSourceProvider` 所需的原始岗位列表：
  {title, description, required_skills[], soc_code, category, level}

依赖：仅标准库（csv/tablib 不需要）。
用法：python ingest.py --src db_30_3 --out snapshot.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict


def _read_tsv(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [row for row in reader]


def _col(row: dict, *candidates: str) -> str:
    for c in candidates:
        if c in row and row[c] is not None:
            return row[c].strip()
    return ""


def convert(src_dir: str) -> list[dict]:
    occ = _read_tsv(os.path.join(src_dir, "occupation_data.txt"))
    skills = _read_tsv(os.path.join(src_dir, "skills.txt"))
    occ_skills = _read_tsv(os.path.join(src_dir, "occupation_skills.txt"))
    tech = _read_tsv(os.path.join(src_dir, "technology_skills.txt"))
    tasks = _read_tsv(os.path.join(src_dir, "occupation_tasks.txt"))

    skill_name = {_col(r, "element_id"): _col(r, "element_name") for r in skills}

    # 职业-技能：取 LV 量表（水平），按值排序取前 N
    occ_to_skills: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for r in occ_skills:
        if _col(r, "scale_id") != "LV":
            continue
        soc = _col(r, "onet_soc_code")
        sid = _col(r, "element_id")
        try:
            val = float(_col(r, "data_value") or 0)
        except ValueError:
            val = 0.0
        occ_to_skills[soc].append((val, skill_name.get(sid, sid)))

    # 职业-技术栈
    occ_to_tech: dict[str, list[str]] = defaultdict(list)
    for r in tech:
        soc = _col(r, "onet_soc_code")
        name = _col(r, "technology_name", "commodity_title", "example")
        if name:
            occ_to_tech[soc].append(name)

    # 职业-任务（丰富描述）
    occ_to_tasks: dict[str, list[str]] = defaultdict(list)
    for r in tasks:
        soc = _col(r, "onet_soc_code")
        t = _col(r, "task", "task_statement")
        if t:
            occ_to_tasks[soc].append(t)

    out: list[dict] = []
    for r in occ:
        soc = _col(r, "onet_soc_code")
        title = _col(r, "title", "onet_soc_title")
        desc = _col(r, "description")
        skills_sorted = [s for _, s in sorted(occ_to_skills.get(soc, []), reverse=True)][:20]
        techs = occ_to_tech.get(soc, [])[:15]
        task_txt = " ".join(occ_to_tasks.get(soc, [])[:5])
        description = (desc + " " + task_txt).strip()
        required = skills_sorted + techs
        # 职能分类（按 SOC 大类前缀粗分）
        prefix = soc.split("-")[0] if soc else ""
        category = {
            "15": "engineering", "11": "management", "13": "business",
            "15": "engineering", "17": "design", "43": "office",
        }.get(prefix, "other")
        level = "mid"
        val = occ_to_skills.get(soc, [(0, "")])[0][0] if occ_to_skills.get(soc) else 0
        if val >= 4.5:
            level = "senior"
        elif val <= 3.0:
            level = "junior"
        out.append({
            "title": title,
            "description": description,
            "required_skills": required,
            "soc_code": soc,
            "category": category,
            "level": level,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="db_30_3", help="O*NET 解压目录")
    ap.add_argument("--out", default="snapshot.json", help="输出快照路径")
    args = ap.parse_args()
    if not os.path.isdir(args.src):
        raise SystemExit(f"未找到 O*NET 目录: {args.src}（见 README.md 下载步骤）")
    data = convert(args.src)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已转换 {len(data)} 个职业 → {args.out}")


if __name__ == "__main__":
    main()
