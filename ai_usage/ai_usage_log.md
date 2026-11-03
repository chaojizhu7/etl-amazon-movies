# AI 使用记录汇总

> 按时间追加。每条来自对话回答末尾的"AI 使用记录"。
> AI 生成的值不作为事实来源；人工验证方式须逐条落实。

---

## 2026-11-03 · 环境搭建与仓库初始化

- **工具/模型**：MiMoCode（mimo agent）+ winget + psql + py -3 + curl + git
- **用途**：下载元数据脚本、Python venv 与依赖、PostgreSQL 17.11 安装与 etl_movie 建库、磁盘内存估算、环境自检脚本、git 仓库结构
- **被采纳的输出**：`src/` 5 个工具脚本；requirements.txt（51 包）；估算表（实测 4 项）；env_check 核对表；目录骨架与 .gitignore
- **人工验证方式**：运行 `src/env_check.py` 看核对表全 PASS；psql 连接 etl_movie；`git status` 确认大文件被忽略

## 2026-11-03 · 人工核验（用户执行）

- **人工验证方式**：①抽查 2 条记录字段全部一致；②git log 有提交；③git status 无大文件；④目录 10 个齐全
- **结果**：**全部通过**

## 2026-11-03 · 评论解析与统计核对

- **工具/模型**：MiMoCode（mimo agent）+ Python 3.14.7 venv
- **用途**：movies.txt.gz 全量流式解析，与官方统计核对
- **被采纳的输出**：`src/stream_review_stats.py`；实测 7,911,684 评论 / 253,059 ASIN / 889,176 用户 / 1997-08~2012-10 四项全部 MATCH
- **人工验证方式**：重跑统计脚本核对数字；Get-FileHash 比对 SHA-256；抽查报告 2.2 表格（用户已完成抽查，见上方核验记录）

## 2026-11-03 · 任务3 电影页面筛选（分类）

- **工具/模型**：MiMoCode（mimo agent）+ Python 3.14.7 venv + psql/PostgreSQL 17.11 + IMDb datasets（title.basics/title.principals/title.crew/name.basics，本地离线）
- **用途**：对产品建立 movie/non_movie/collection/tv_series/concert/other/pending_review 七类分类；制定带版本号识别规则（v0.1）；标题规范形与 IMDb 精确匹配、演职员/年份/变体/碰撞过滤四级消歧；分类结果落库与统计产出
- **原始提示词**：任务3 要求（分类体系 7 类；规则写 rules/ 带版本号；不能仅凭标题含 DVD/Blu-ray 判定；依据分类导航/商品描述/导演演员/片长/IMDb 匹配综合判断；证据不足标 pending_review 不许猜；无电影映射的评论仍保留；先对 success 快照分类、access_restricted 不猜；输出分类分布表、每类 5 个样例含依据、字段覆盖率、成功采集率）
- **被采纳的输出**：`rules/movie_classification_v0.1.md`；`sql/03_product_classification.sql`；`src/classify_products.py`；`product_classification`（253,059 行）+ `product_movie_candidate`；`logs/summary/classification_stats.json`/`classification_samples.json`；`data/raw/asin_movies.txt`；报告 4.3 节（run_20261103-01：movie 227/tv_series 19/collection 3/concert 1/other 6/non_movie 0/pending_review 252,803，evidence 245/inferred 11）
- **人工验证方式**：①psql 抽查 `product_classification` 样例的 classification/evidence JSONB 与快照原文对照（如 Iron Man 演职员消歧 tt0371746、Back to the Future Trilogy 合集 3 部候选、Polar Express Gift Set 判 movie 非 collection）；②`Get-Content logs/summary/classification_stats.json` 核对分布合计=253,059；③抽查 pending_review 样例确认受限页未被猜类；④重跑 `src/classify_products.py` 确认幂等（数字一致或随快照增长单调更新）

## 2026-11-03 · 任务3 人工复核处置（10 样本）与 Jaws 剥离缺陷修复

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + Python 3.14.7 venv
- **用途**：按人工复核结论处置 10 个 IMDb 匹配样本（5 高置信确认正确、5 低置信错配/缺陷/标注）；修复剥离表顺序缺陷（"Nth Anniversary Edition" 先剥致残留）；重跑匹配刷新报告 4.3；复核标记写入 evidence.manual_review（跨重跑保留）；产出人工复核记录
- **原始提示词**：复核结果 6 项（Soy leyenda 确认错配→tt0780504 留任务4；Jaws 确认剥离 bug→修复重跑刷新 4.3；Les Mis tconst 标 unverified 留任务4/5；Ben-Hur 标 version_unverified 留任务4；Spark of Insanity 标 no_match+seller_title_missing_artist_prefix 不硬匹；修复只影响分类/匹配统计、不影响高置信与评论数据；复核结果写 report/人工复核记录.md）
- **被采纳的输出**：`src/classify_products.py` 剥离表修复+标记保留逻辑；`run_20261103-02` 统计刷新（movie 316/tv_series 25/collection 4/concert 1/other 7/pending_review 252,706）；10 条 `manual_review` 标记；`report/人工复核记录.md`；报告 4.3 同步
- **人工验证方式**：①Jaws 重跑后查 `product_classification` 确认 tt0073195/movie/0.9；②高置信 5 条修复前后 psql 对拍零变化；③`Get-FileHash movies.txt.gz` 核对 SHA-256 `4b6ee09e…cdcc715` 不变、stream_stats 7,911,684 MATCH 不变；④重跑后查 `evidence ? 'manual_review'` 确认 10 条标记存续
