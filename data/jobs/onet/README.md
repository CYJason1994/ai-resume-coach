# O*NET 摄入（CC BY 4.0，美国劳工部）

署名要求（必须保留）：

> Includes information from the O*NET 30.3 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA.

## 步骤

1. 从 https://onetcenter.org/database.html 下载 O*NET 30.3 Database（ZIP，含 Tab 分隔文本）。
2. 解压到本目录（如 `db_30_3/`），确保含：
   - `occupation_data.txt`（职业标题/描述）
   - `skills.txt`（技能元素名）
   - `occupation_skills.txt`（职业-技能关联，scale LV）
   - `technology_skills.txt`（职业-技术栈）
   - `occupation_tasks.txt`（职业-任务陈述，用于丰富描述）
3. 运行：`python ingest.py --src db_30_3 --out snapshot.json`
4. 默认 `JOB_SOURCE=onet` 会读取 `snapshot.json`；缺失时回退 curated。

摄入后运行 `python scripts/seed_jobs.py` 灌库并预计算 `deepseek-embedding` 嵌入。
