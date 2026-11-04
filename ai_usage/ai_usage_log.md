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

## 2026-11-03 · 任务4 合并规则设计（v1.0→v1.1）与合并预估

- **工具/模型**：MiMoCode（mimo agent）+ Python 3.14.7 venv + psql/PG 17.11
- **用途**：设计电影实体合并规则（同一电影定义/候选生成/6 信号验证/置信度公式/三级判定/链一致性/ProductMovie 评论归属）；按审核意见修订 v1.1（S2 年份硬冲突两档化 + 阈值校准流程）；实现 `src/merge_movies.py --estimate` 跑合并预估报告（只算不写库）
- **原始提示词**：任务4 第一步 7 项要求 + 审核结论 6 项（含 |Δ年|≥10 两档修改、manual>50 则放宽 V≥0.55 或 known≥2）+ 收尾指令（更新 CONTEXT.md、TODO 记录、git commit [win2-dev]、今日总结）
- **被采纳的输出**：`rules/merge_rules_v1.md`（v1.1 草案）；`src/merge_movies.py`（预估模式）；`logs/summary/merge_estimate_report.json`（152 对：auto 125 / manual 1 / no_merge 26，链检 0 拒边）；CONTEXT.md §9 进展+TODO；`src/classify_products.py` canon 变音符号修复
- **人工验证方式**：①对照预估样例抽查 auto/no_merge 判定（Iron Man vs Iron Man 2 序数拦截、Watchmen 同片归并、Dracula/Scrooge 别名疑点）；②`Get-Content logs/summary/merge_estimate_report.json` 核对计数与原因分布；③明日按修改确认复核 v1.1 定稿项

## 2026-11-04 · 任务4 合并执行（v1.2→v1.3）与人工判定落库

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + gzip 快照解压 + git
- **用途**：v1.2（title_conflict 高 V 降 manual）与 v1.3（证据只采信已消歧/唯一/成对共享 tconst，未消歧节点 auto ceiling）修订；分类刷新 run_20261104-01；合并执行 run_20261104-01/02（含 ±tolerance 门槛与证据污染修复重执行）；Robin Hood 对快照原文补充证据（4h57m/2h36m 原文、无合集字样、140+156≈297 核算）；6 对人工判定落库 run_20261104-03（manual_merge、别名、dual_cut_bundle 标注、链检、最终统计）
- **原始提示词**：v1.2 决策 2 项（Dracula/Scrooge 人工判；分类刷新后预估 ±10 门槛）；选 B 三类样例+直接执行；6 对裁定（5 合并+Robin Hood 补充证据后合并，含 dual_cut_bundle 标注）；交付前后统计/复核记录/AI 记录
- **被采纳的输出**：`rules/merge_rules_v1.md` v1.3；`sql/04_movie_merge.sql`（含 manual_merge 枚举）；run_20261104-02 合并落库（447 版本→363 实体/64 簇）+ run_20261104-03 人工判定（manual_merge 6、mv_000006=5 成员、别名 2、dual_cut_bundle）；`merge_eval_sample.csv`（100 对待标注）；`merge_final_stats.json`；`report/人工复核记录.md` 6 对全量判定
- **人工验证方式**：①psql 查 `movie_merge_decision WHERE run_id='run_20261104-03'`（6 行）与 mv_000006 成员数=5；②`movie_alias WHERE source='manual'`=2（Scrooge、Bram Stoker's Dracula）；③B002ZG98VE 的 `product_movie.evidence` 核对 dual_cut_bundle(conf 0.85)；④解压两件 Robin Hood 快照核对 Runtime 原文 4h57m/2h36m

## 2026-11-04 · 任务5 人物姓名归一与消歧（v1.0→v1.1 + 执行）

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + Python 3.14.7 venv + IMDb（title.principals/title.crew/name.basics 离线）
- **用途**：人物消歧规则设计与 v1.1 修订（年代硬冲突加 ≥5 部作品门槛、era_basis=inferred 口径）；执行人物合并（4,957 个人员记录 → person_id；S1–S5 信号；姓名相同≠同一人/缩写封顶 manual 红线；链一致性拒边降级）；movie_person 多角色建模；5+5 实例与 100 对评价样本
- **原始提示词**：任务5 七项要求 + 审核意见 6 项（年代硬冲突加作品数门槛、稀疏降 manual；生卒年缺失用作品 startYear 推算标 inferred；其余维持）+ 直接执行免预估 + 交付 5+5/前后统计/100 对
- **被采纳的输出**：`rules/person_merge_rules_v1.md` v1.1；`sql/05_person_merge.sql`；`src/merge_persons.py`；run_20261104-04（842 对：auto 362/manual 378/no_merge 102；person_entity 4,595；movie_person 5,770）；`person_eval_sample.csv` 100 对；`person_merge_stats.json`
- **人工验证方式**：①核验 5 组合并实例共同作品/年代证据（John Belushi/Ingrid Bergman 等页面署名→nconst）；②核验 5 组不合并实例（Lilly vs Lana Wachowski 近似名封顶 manual、Paul Verhoeven/Trevor Howard 同名不同人硬冲突）；③psql 查 `person_merge_decision WHERE run_id='run_20261104-04'` 分布与 `movie_person` 角色行；④标注 person_eval_sample.csv 后出 P/R/F1

## 2026-11-04 · 任务5 人工复核 2 对（人物合并反例）入档

- **工具/模型**：MiMoCode（mimo agent）+ write 工具 + git
- **用途**：2 对人物合并反例人工判定入档（Lilly×Lana Wachowski 不合并=姐妹共同执导；Errol Flynn×Errol Flynn 不合并=同名不同人概率高，留 nconst 桥接改判通道）
- **原始提示词**：人工复核 2 对判定（均不合并，含理由与条件改判说明）；写入 report/人工复核记录.md
- **被采纳的输出**：`report/人工复核记录.md` 第三轮（2 对判定+理由+处置计划）；提交
- **人工验证方式**：①对照记录核对两对判定与理由原文；②落库后查 `person_merge_decision` 中 reason 以 manual_confirm 开头的 2 行；③确认 Lilly/Lana Wachowski 两 person_id 仍独立

## 2026-11-04 · 任务5 判定落库 + 报告 6.2 回填 + 任务6 上映信息补全

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + Python 3.14.7 venv + git
- **用途**：2 对人物判定落库（Wachowski/Errol Flynn → manual no_merge，含 pending_nconst_bridge）；报告 6.2 回填（person_id 规则/归一策略/5+5 实例/MoviePerson/准确性统计）；任务6 缺失上映信息补全（语义分层：release_year=imdb.startYear precision=year 不补 1.1、商品发售日期仅入 edition 域、earliest_review_time 与 inferred_release_bound 分列、缺失误因词表）
- **原始提示词**：落库 2 对；回填 6.2；任务6 六项要求（语义分层三层/仅年份不补日期/补全留来源原始值抽取时间规则置信度/评论时间只作 earliest_review_time+条件上界另存 inferred/查不到 NULL+missing_reason/输出补全统计）直接执行
- **被采纳的输出**：`sql/06_release_completion.sql`、`src/complete_release_info.py`、run_20261104-05（release_year 260/363 补全=255 resolved+5 候选一致、precision year=260、inferred_bound 360、edition_sale_date 243/447、earliest_review 360）、`release_completion_stats.json`、报告 6.2 回填
- **人工验证方式**：①psql 查 `release_facts WHERE run_id='run_20261104-05'` 抽样核对 value_raw/value_norm/match_rule/置信度；②确认 release_year 全为 YYYY（无 1 月 1 日补全）；③抽查 edition_sale_date 行 asin 级且未混入 release_year；④Wachowski/Errol Flynn 决策行 manual_confirm 2 条

## 2026-11-04 · 任务7 字段级来源冲突选择 + LXG 合并与同 tconst 排查

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + git
- **用途**：mv_000256/257 manual_merge（同名同 tconst 同年份）；排查同 tconst 未合并实体对（56 对清单）；任务7 来源冲突选择——规则链 R1–R7（语义边界/匹配可靠性/来源权威/语义一致/日期精度/采集时间仅平局/人工继承）、候选全保留三态留痕、多值字段角色分离、conflict 不编造；5+ 冲突案例与决策统计
- **原始提示词**：抽查判定（组1 维持 pending_review、组2 年份确认、LXG 合并 manual_merge）+ 排查同 tconst 对 + 任务7 六项要求与三件交付（规则文档含阈值版本/≥5 冲突实例/自动人工冲突统计）直接执行
- **被采纳的输出**：LXG 合并（merged 簇 65）；56 对清单；`rules/field_selection_rules_v1.md` v1.0；`sql/07_field_selection.sql`；`src/select_fields.py`；run_20261104-07（断言 5,368：自动 4,432/人工 37/冲突 396）；`field_selection_stats.json`
- **人工验证方式**：①`SELECT status,decision_basis,count(*) FROM field_assertion WHERE run_id='run_20261104-07' GROUP BY 1,2` 对照统计；②抽 conflict 行核对"不删不编造"（候选值与来源俱在）；③Cinderella Man 1917/2005 两行均保留；④Sound of Music genres 双角色行（work_genre vs seller_category_tag）

## 2026-11-04 · 任务7 收尾（A 类 56 对落库）+ 任务8 评论清洗与重复识别

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + Python 3.14.7 venv + git
- **用途**：56 对同 tconst 实体处置落库（A 类 32 合并+puss 不合并，B 类 8 对传递吸收、15 对待判）；5 对 B 类证据提取；任务8 评论去重——去重目标声明（同评论重复 vs 跨版本相似）、候选生成、逐字符核验、ReviewMapping、helpfulness 校验、评分计算声明
- **原始提示词**：A 类三规则（同 tconst 同年份/同年缺失→合并、puss 不合并）+ B 类 5 对证据 + 任务8 八项要求直接执行
- **被采纳的输出**：run_20261104-08 落库（movie_entity 363→330）；5 对证据表；`sql/08_review_mapping.sql`、`src/clean_reviews.py`；run_20261104-09（7,911,684 记录：候选 13,893 组/19,098 对、确认重复 4,566 组/5,146 副本、判不同 10,041 组、异常 43、mapping unique 7,901,972+primary 4,566+copy 5,146）；`review_cleaning_stats.json`
- **人工验证方式**：①`SELECT map_status,count(*) FROM review_mapping GROUP BY 1` 合计=7,911,684；②抽 duplicate_copy 与 primary 同 logical_id 核对逐字符一致；③抽"判不同"组核对差异字段；④43 条 helpfulness_num_gt_den 异常可查 review_anomaly

## 2026-11-04 · 任务8 收尾（B 类 4 对落库）+ 任务9 字段级数据血缘

- **工具/模型**：MiMoCode（mimo agent）+ psql/PG 17.11 + git
- **用途**：B 类 4 对合并落库（Notebook/Snow White/Braveheart/Aliens，Metropolis 维持）；任务9 血缘模型——SourceRecord/FieldAssertion/最终值/处理链四表、18,405 断言装配、四种查询脚本（movie+字段/评论→作品/源记录→影响/人物→别名与作品）与示例运行
- **原始提示词**：4 对合并裁定 + Metropolis 维持 + 56 对最终汇总 + 任务9 六项要求（OpenLineage 字段级模型/最终值可解释/未采用可查/多源多值/四种查询/DDL+脚本+示例）直接执行
- **被采纳的输出**：run_20261104-10 合并（movie_entity 326）；56 对终版（44/1/11）；`sql/09_lineage.sql`、`src/build_lineage.py`、`lineage/query_0[1-4]_*.sql`；run_20261104-11 血缘层（源 519/断言 18,405/最终值 2,076/步骤 11,589）
- **人工验证方式**：①query_01 查 mv_000198 release_year 看 conflict 两候选与理由；②query_02 查 B00005JPS8/序号 5853365 走通评论→作品链；③query_03 查 sr_page_B00005JPS8 看 7 条影响与 in_final 标志；④query_04 查 p_000001 看双来源别名+merge 步骤+作品角色
