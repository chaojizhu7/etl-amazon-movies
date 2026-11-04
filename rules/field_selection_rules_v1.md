# 字段级来源冲突选择规则 field_selection_rules

- **规则版本**：v1.0（文件名 `field_selection_rules_v1.md`）
- **生效日期**：2026-11-04（直接执行，按任务7 要求产出）
- **适用范围**：任务7 同一字段多来源候选的选择/拒绝/冲突保留（FieldAssertion + FieldSelection 落地）
- **执行脚本**：`src/select_fields.py`（幂等；DDL `sql/07_field_selection.sql`）
- **上游依据**：`report/字段覆盖矩阵.md`（来源标签与缺失词表）、`rules/merge_rules_v1.md` v1.3、`rules/person_merge_rules_v1.md` v1.1

## 1. 原则（作业红线）

1. **可复核**：每个候选值全量保留（value_raw 原文 + source + source_locator + extracted_at + match_rule + confidence）；选中标 `selected`，拒绝标 `rejected`+理由，无法决策标 `conflict`——**三态都留痕**。
2. **禁止裸规则**：不得仅以"最后抓到的"或"多数票"作为理由；采集时间只可作**最后顺位平局裁决**（match_rule 显式记 `tiebreak_crawl_time`），多数票只可作证据之一且须写明计票口径。
3. **语义不冒充**（铁律3）：作品级字段（title/release_year/genres/directors/starring）以 imdb（tconst/nconst 定位）为权威源；产品页值保留为产品级/署名级证据；**商品发售日期永不进入 release_year**（语义边界已在任务6固化）。
4. **多值字段可合并但角色不混**：genres 等多值字段允许并集，每个成员带 source 标签与 `value_role`（work_genre / seller_category_tag / page_credit / alias）；不同角色不得互相覆盖。
5. **不编造**：无法可靠决策 → `conflict` 状态保留，交人工/后续任务。

## 2. 选择规则链（按序短路，可审计）

| 序 | 规则 | 内容 | 决策依据标签 |
|----|------|------|--------------|
| R1 | 语义边界 | 值必须符合字段语义域（release_year=作品年 YYYY；edition_sale_date 只入 edition 域）；越界候选直接 rejected（reason=`semantic_out_of_scope`） | rule_auto |
| R2 | 匹配可靠性 | 候选证据锚定级别：resolved_tconst/nconst > 唯一候选 > 候选全体一致 > 候选分歧。仅"全体一致"及以上可自动选定 | rule_auto |
| R3 | 来源权威性 | 作品级字段：imdb（tconst 定位）> product_page（asin+快照哈希）> inferred；edition 级字段：product_page 唯一源。冲突时高权威 selected，低权威保留为 candidate/alias（不删除） | rule_auto |
| R4 | 语义一致 | 选定值须与既有已核实值不矛盾（year vs era_span、director vs movie_person 角色、多片标记 vs 单一作品）；矛盾 → 降 conflict | rule_auto |
| R5 | 日期精度 | 年精度只输出 YYYY（precision=year）；日精度仅评论/发售日期；禁止用更高精度值回填低精度字段（不补 1 月 1 日） | rule_auto |
| R6 | 采集时间 | 仅当 R1–R5 后仍平局，按 extracted_at 较晚者选定，match_rule 记 `tiebreak_crawl_time`（唯一允许时间裁决的场合） | rule_auto |
| R7 | 人工判定继承 | 人工复核结论（manual_confirm 系）为最高依据，标 `manual_inherit`，覆盖自动规则结果并保留被覆盖候选 | manual_inherit |

## 3. 字段级规则

| 字段 | 候选来源 | 选定规则 | 多值/角色 |
|------|----------|----------|-----------|
| title | imdb primaryTitle/originalTitle（tconst）；product_page title_raw；movie_alias | resolved tconst 存在 → imdb primaryTitle 为 canonical_title；否则唯一产品页标题。其余全保留 role=alias | 单值 canonical + alias 集 |
| release_year | imdb.startYear（各候选 tconst）；产品页年份线索 | R2 链（resolved 一致 > 候选全体一致）；候选分歧 → 全部 `conflict` 保留（不得挑一个） | 单值 |
| genres | imdb.genres（tconst）；product_page genre 字段 | 并集；imdb 成员 role=work_genre（selected）；产品页成员 role=seller_category_tag（selected，标签不冒充） | 多值合并 |
| directors | imdb title.crew（nconst→name）；产品页 (Director) 署名 | imdb 集 selected（role=work_crew）；产品页署名 role=page_credit 保留；产品页出现 imdb 之外的人名 → 该名 `conflict`（供任务5/人工消歧，不删） | 多值 |
| starring | imdb principals（ordering≤10，nconst→name）；产品页 (Actor) 署名 | 同 directors；产品页署名不冒充完整 cast | 多值 |

## 4. 阈值与置信度

- 自动选定（selected/rule_auto）：匹配可靠性 ≥ "候选全体一致" 且语义检查通过；confidence = 来源基础置信 × 锚定系数：resolved_tconst 0.95 · 唯一候选 0.90 · 全体一致 0.85 · manual 1.0。
- conflict 保留：confidence=NULL（不赋值），reason 记录冲突成员。
- rejected：必须写 reject_reason（词表：`semantic_out_of_scope` / `superseded_by_higher_authority` / `contradicts_verified_value` / `duplicate_candidate` / `role_mismatch`）。

## 5. 合并后可查询性（作业要求 6）

电影/人物合并后：**原评论**源不动（movies.txt.gz 哈希不变，经 asin→product_movie→movie_id 可定位）；**版本属性**在 product_classification / release_facts（asin 级）可查；**来源记录**在 movie_alias / person_alias / *_decision / field_assertion（source+source_locator）可查。任何选择决策不删除候选行。

## 6. 决策统计口径

- 自动决策数 = status=selected 且 decision_basis=rule_auto 的断言行数
- 人工决策数 = decision_basis=manual_inherit 的行数（继承人工复核结论）
- 保留冲突数 = status=conflict 行数（按字段分列）

## 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-11-04 | 初稿：选择规则链 R1–R7（语义边界/匹配可靠性/来源权威/语义一致/日期精度/采集时间仅平局/人工继承）；多值字段角色分离；rejected 必附理由；conflict 保留不编造；阈值与置信度；合并后可查询性口径；决策统计口径 |
