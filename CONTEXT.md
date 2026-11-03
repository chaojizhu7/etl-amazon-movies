# CONTEXT.md · 新会话上下文（Amazon 电影评论 ETL 作业）

> 用途：新 Agent 窗口快速了解项目现状与规范。本文件由窗口 1 生成（2026-11-03）。
> 工作区根目录：`C:\Users\28746\Desktop\etl-amazon`
> Git：https://github.com/chaojizhu7/etl-amazon-movies （main，本地已领先远端若干提交）

---

## 1. 项目背景

数据存储与管理课程 ETL 作业：**Amazon 电影评论与多源电影信息集成**。
构建可重复运行的 ETL 流程：全量抽取 Stanford SNAP Amazon movie reviews（movies.txt.gz）→ 产品页面采集 → 电影/人物实体消歧合并 → 信息补全与冲突处理 → 装载数据库 → **字段级数据血缘**。

**关键要求摘要**（详见 `report/报告草稿.md` 与桌面 `ETL作业要求.pdf`）：
- 全量处理：评论 7,911,684 条、产品 253,059 个（不同 ASIN）、用户 889,176 个——已全量核对 MATCH
- 严禁 ASIN 当 movie_id；作品/版本/评论/人物四类实体分开建模
- 每个产品须有处理状态；无法取得的页面保留原因和证据；互斥状态数之和 = ASIN 总数
- 电影、人物各 ≥100 对人工标注评价样本，报告 Precision/Recall/F1
- 字段级血缘 4 个必答查询；ETL 工具（Airflow）组织作业；幂等 + 中断恢复
- 5 个可执行查询 + 10~15 分钟现场展示；AI 辅助须记录工具/用途/人工验证方式

## 2. 目录结构

| 目录 | 内容 | 对应作业章节 |
|------|------|--------------|
| `data/raw/` | movies.txt.gz、download_meta.json；imdb/（5 表+checksums.sha256+download_meta.json）；asin_batch_010/batch_001 清单 | 二、数据来源；任务1 |
| `snapshots/` | pages/（页面快照 gz）；index*.csv（快照索引含 content_sha256）；crawl_manifest*.jsonl（采集清单） | 任务2 |
| `src/` | Python 脚本（解析/统计/扫描/校验）；crawler/（Scrapy 项目） | 任务1~8 代码 |
| `sql/` | 01_crawl_queue.sql、02_crawl_attempt.sql 等 DDL | 八、目标数据模型 |
| `etl/` | Airflow 安装/初始化脚本、dags/、batch 计划、contingency_plan.md | 八、ETL 架构 |
| `rules/` | 带版本号规则文件（如 crawl_priority_v0.1.md） | 任务4/5/7/8 |
| `logs/summary/` | 运行统计 JSON/小日志（大日志不进 git） | 十、可复现与恢复 |
| `lineage/` | 血缘查询脚本（待开发） | 九、字段级血缘 |
| `ai_usage/` | ai_usage_log.md（AI 使用记录汇总） | 十一、提交内容 |
| `report/` | 报告草稿.md、数据源策略.md、字段覆盖矩阵.md | 十一、项目报告 |
| `requirements.txt` / `db.env.example` / `.venv/` | Python 依赖锁 51 包 / DB 配置模板 / Windows 虚拟环境 | — |

## 3. 数据现状（截至 2026-11-03）

| 数据 | 状态 | 实测数字 |
|------|------|----------|
| movies.txt.gz | ✅ 已解析 | 7,911,684 条评论 = 有效 7,911,684 + 隔离 0；253,059 ASIN；889,176 用户；时间 1997-08-20 ~ 2012-10-25 UTC；SHA-256 `4b6ee09e…cdcc715` |
| IMDb 5 表 | ✅ 已下载+剖析 | basics 12,921,360 行（movie 759,812，有 startYear 647,293=85.2%）、crew 12,918,857（有导演 55.4%）、name.basics 15,783,070、principals 102,659,089、ratings 1,728,260；哈希在 `data/raw/imdb/checksums.sha256` |
| product_crawl_queue | ✅ 已建表+装载 | 253,059 行；分层 high 20,985 / medium 83,345 / low 148,729；candidate_url 全构造 |
| 采集扫描 | **进行中（窗口 1，波次模式）** | 截至 2026-11-03 收工：**16 波 / 6,152 ASIN 已尝试（2.43%）**；状态分布 success 471 / access_restricted 5,641 / fetch_failed 45 / parse_failed 5 / pending 246,897（之和=253,059 ✅）；下一步 wave 017+（14,823 high 层待跑 + movie 清单 320 持续并入），用户说"继续"再跑；审计日志 `logs/audit/操作日志.md` |
| 试采/验证批 | ✅ 完成 | 20+10 ASIN：success 2（B0070225F4、B00005JPS8）、access_restricted 27、fetch_failed 1；验证页特征=3,781B+captcha 标记 |

## 4. 已建立的规范

- **代码风格**：Python 3.14（Windows `.venv`）+ PostgreSQL SQL + Airflow 2.10.4（WSL）；脚本放 `src/`，幂等可重复执行；中文文件只用 edit/write 工具修改（防乱码）
- **命名规则**：脚本 `snake_case.py`；SQL `NN_名称.sql`；规则 `rules/<类别>_v<版本>.md`；批次 `asin_batch_NNN.txt`；清单 `crawl_manifest_<batch>.jsonl`
- **规则版本号**：`v<主>.<次>`（如 v0.1）；规则变更升版本并在提交信息带 `[rules=vX.Y]`
- **run_id 格式**：`run_<YYYYMMDD>-<序号>` 或 `scan_<rules_version>_<batch>`；写入 ETLRun/attempt 记录
- **提交信息规范**：`<type>(<scope>): <简述> [parser=vX.Y][rules=vX.Y][run_id=...]`（type: init/feat/fix/data/rules/etl/docs/test）
- **AI 使用记录格式**（每次回答末尾必附，汇总到 `ai_usage/ai_usage_log.md`）：
  `工具/模型、日期、用途、原始提示词、被采纳的输出、建议的人工验证方式`

## 5. 数据库信息

- **连接**：PostgreSQL 17.11，`localhost:5432`，库 `etl_movie`，用户 `postgres`，口令见 `db.env.example`（本机开发口令 `postgres`，**不提交仓库**）
- **已建表**：
  - `product_crawl_queue`（253,059 行）：采集队列——asin(PK)、review_count、priority(1=最高)、priority_tier(high/medium/low)、candidate_url、status(9 态 CHECK 约束)、created_at/updated_at
  - 状态机：`pending → crawling → success / page_not_found / access_restricted / timeout / parse_failed / fetch_failed → permanently_unreachable`（另有 `not_crawled` 用于跳过）
- **DDL 就绪待执行**：`crawl_attempt`（sql/02_crawl_attempt.sql）——采集证据链：asin、run_id、attempt_no、http_status、response_bytes、content_feature(product_page/verification_page/not_found/error/other)、evidence_path、content_sha256、attempted_at
- **规划中**（作业逻辑模型，尚未建表）：Movie、ProductEdition、ProductMovie、Reviewer/Review（source_review_id）、ReviewMapping、Person/PersonAlias、MoviePerson、SourceRecord、FieldAssertion、MergeDecision/FieldSelection、ETLRun/QualityIssue

## 6. 铁律（每条都不可违反）

1. **AI 输出不作事实来源**：所有数字和结论必须来自实际运行结果或可定位证据（命令输出、文件哈希、快照）；不确定标"待确认"，禁止编造
2. **保留原始值**：raw_value 与 normalized_value 分列保存；原文不截断不改意
3. **来源标签不冒充**：IMDb 值永远标 `source=imdb`（tconst/nconst 定位）；产品页值标 `source=product_page`（URL+快照哈希）；**替代源数据绝不冒充产品页数据**
4. **推断值标 inferred**：`status=inferred` + 推断条件 + confidence；推断值不得覆盖已核实值；仅知年份不补 1 月 1 日
5. **受限页不标成功**：HTTP 200 验证页（3,781B+captcha 标记）判别为 access_restricted，永不标 success
6. **不绕验证/不登录/不破封禁**：受限就如实记录原因和证据（验证页快照留档），不得伪造采集结果
7. **每个产品均有处理状态**：至少尝试过一次并留证（crawl_attempt）；互斥状态数之和 = 253,059
8. **原始记录全保留**：不去原始评论、不删证据快照、不回退改判状态

## 7. 当前进度与分工

| 任务 | 状态 | 负责窗口 |
|------|------|----------|
| 任务1 全量评论抽取 | ✅ 完成（统计 MATCH） | 窗口 1 |
| 任务2 产品采集 | **进行中**：波次扫描模式（每波 ~391 ASIN / 10 分钟，"继续"推进，断点续爬）；**2026-11-03 已完成 16 波（6,152 ASIN，2.43%）**，累计真实商品页 471；范围 B 生效（movie 优先 + 非电影 high） | **窗口 1** |
| 任务3 电影页面筛选 | ✅ 完成（七类分类落库 253,059 行 + 人工复核处置；rules v0.1 / run_20261103-02） | 窗口 2 |
| 任务4 电影实体合并 | **进行中**：规则 v1.1 草案 + 预估报告完成，待确认后执行（见 §9） | 窗口 2 |
| 任务5 人物消歧 | 未开始（需人工标 100 对评价样本） | 待定 |
| 任务6/7/8 补全/冲突/去重 | 未开始 | 待定 |
| 任务9 字段血缘 | 未开始 | 待定 |
| ETL 工程（Airflow） | hello-world 已验证；fast_scan_dag 等 5 DAG 设计稿在 `etl/contingency_plan.md` | 窗口 1 |

**已过里程碑**：环境搭建（venv/PG 17.11/Airflow 2.10.4）→ 统计核对 MATCH → git 仓库+GitHub → 采集合规+试采 → IMDb 补全策略+字段覆盖矩阵 → 采集队列 253,059 → 验证批决策（9 受限停止）→ 备用方案 v0.2 确认。

## 8. 任务 3 提示词（窗口 2 执行）

> 你是本 ETL 作业任务 3 的执行者。工作区 `C:\Users\28746\Desktop\etl-amazon`，先读本文件与 `report/报告草稿.md` 第 4.3 节。
>
> **任务：电影页面筛选（作业任务 3）**
> 1. 对采集队列中的产品建立分类：**电影 / 非电影 / 合集 / 剧集 / 演唱会或其他 / 待确认**，每条给出分类依据与证据（快照内容、IMDb titleType、产品页特征）。
> 2. **不能仅凭标题含 DVD/Blu-ray 认定为电影**；证据不足标"待确认"，不许猜。没有电影映射的评论仍保留。
> 3. 合集产品（一盘多片）标记 collection 并记录候选多电影映射（ProductMovie 多对多预留）；剧集/单集单独定义，不与电影混计。
> 4. 产出：采集状态分布表、电影识别数量、字段覆盖率（分母=已识别作品；另报全产品识别覆盖率）；离线快照/第三方(IMDb)/实时采集分别统计，不得混为同一成功指标。
> 5. 数据依据：`snapshots/crawl_manifest*.jsonl` + `snapshots/pages/` 快照 + `product_crawl_queue` + IMDb `data/raw/imdb/title.basics.tsv.gz`（titleType 列）。
> 6. 分类结果落库（建议表 `product_classification`，DDL 进 `sql/`），规则写 `rules/` 带版本号；结果回填 `report/报告草稿.md` 4.3 节。
> 7. 遵守铁律（本文件第 6 节）：实测为准、来源不冒充、推断标 inferred、受限页不标成功、不绕验证。每次回答末尾附"AI 使用记录"（工具/模型、日期、用途、原始提示词、被采纳的输出、人工验证方式）。
> 8. 完成后更新 `ai_usage/ai_usage_log.md` 与 `report/报告草稿.md`，提交信息规范见第 4 节。

---

## 9. 任务4 进展与 TODO（窗口 2，2026-11-03 收尾更新）

### 合并规则审核处理（`rules/merge_rules_v1.md`）

- v1.0 草案审核结论：权重 ✅、auto_merge 门槛 ✅（执行前先跑预估）、manual_review 区间 ✅、片长 40% ✅、movie_id 格式 ✅
- **审核修改点（已写入 v1.1 草案）**：S2 年份硬冲突两档化——`|Δ年|≥10 且标题关键词不重合` → 硬冲突（重拍）；`|Δ年|≥10 但标题高度相似` → **降为 manual_review**（疑修复/重制版）；其余硬冲突（续集序数、导演异+主演零重合、片长比>1.6 且多片标记）不变
- 阈值校准流程（v1.1）：预估 manual_review > 50 对则放宽（V≥0.55 或 known≥2），硬冲突不动

### 预估报告（`logs/summary/merge_estimate_report.json`，316 个 movie 产品）

- 候选对 152 → **auto_merge 125 / manual_review 1 / no_merge 26**（manual 未超 50，无需放宽）
- 链一致性预检：125 条 auto 边全部通过 clique 检查，0 拒边
- no_merge 构成：续集序数 7 · 重拍模式（导演不同+主演零重合）14 · 标题冲突 5
- **预估发现（待定夺）**：title_conflict 中 3 组疑似同片别名（Dracula ≡ Bram Stoker's Dracula；Scrooge ≡ A Christmas Carol 各版，验证信号 V≈0.93–1.0），因 aka 表缺失被标题冲突挡下——建议 v1.2 候选项：S1 conflict 且 V≥0.80 → manual_review 而非 no_merge

### TODO（2026-11-04）

1. **合并规则修改确认**：merge_rules_v1.md 待修改点——`|Δ年|≥10 但标题相似 → 降为 manual_review`（v1.1 草案已按此实现，随明日修改确认一并定稿）
2. **预估报告要求**：按明日指令重整/补充预估报告
3. **执行合并（确认后）**：交付 `sql/04_movie_merge.sql` + `src/merge_movies.py` 执行模式；100 对人工标注评价（P/R/F1）
4. 小项：`canon()` 已加变音符号剥离（`src/classify_products.py`），分类任务下次重跑吸收；title_conflict 别名疑点是否放宽待定

---
*本文件由窗口 1 生成；如与后续实际状态冲突，以实际运行结果为准。*
