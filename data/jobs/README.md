# 岗位数据来源与版权

本目录存放岗位种子数据，所有外部数据集**仅在明确可商用/CC 协议下引入**，并在 `jobs.source_license` 标注出处。

## 默认源：O*NET 30.3（CC BY 4.0，美国劳工部）

> Includes information from the O*NET 30.3 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA.

- 来源：https://onetcenter.org/database.html （CC BY 4.0）
- 摄入脚本：`data/jobs/onet/ingest.py` → 生成 `data/jobs/onet/snapshot.json`
- 职业结构 + 技能 + 技术栈 + 任务描述，映射为 `jobs` 行
- `jobs.source_code='onet'`, `source_license='CC BY 4.0'`, 含 `soc_code`
- 中文标题/技能映射见 `zh_overlay.json`（自有 IP，可配置）

## 备选：curated 手写/覆盖层（自有 IP）

- 目录：`data/jobs/curated/*.json`
- `jobs.source_code='curated'`, `source_license='proprietary'`
- 用于补充或覆盖特定岗位；O*NET 快照缺失时自动回退到此

## 中文 overlay

`data/jobs/zh_overlay.json`：按 `soc_code` 或 `title` 映射中文标题与技能同义词，
使匹配对中文简历可用。属自有知识产权，可配置开关 `JOB_SOURCE_ZH_OVERLAY`。

## 切换数据源

`JOB_SOURCE=onet|json`（见 `.env.example`）。新增源只需实现 `JobSourceProvider` 子类。
