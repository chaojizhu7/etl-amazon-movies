# QA-1 终态报告 · 数据质量与验收

> 依据《ETL 作业要求》第十节"数据质量与验收要求"；对应报告草稿 §9。
> 数据时点：2026-11-04（run_id=run_etl_demo_02 全管线终态；采集 wave 035 后按指令暂停）。
> 所有数字为实测 SQL/统计文件输出，口径逐项标注；缺失/推断/已核实**分别统计**。

## 1. 数量与完整性核对

| 项目 | 官方/应答 | 实测 | 结论 |
|------|-----------|------|------|
| 全量评论 | 7,911,684 | 7,911,684 | ✅ MATCH |
| 全量产品（不同 ASIN） | 253,059 | 253,059 | ✅ MATCH |
| 全量用户 | 889,176 | 889,176 | ✅ MATCH |
| 时间范围 | 1997-08 ~ 2012-10 | 1997-08-20 ~ 2012-10-25 UTC | ✅ |

**产品清单互斥最终状态数之和 = 253,059 ✅**（两套口径各自闭合）：
- 采集状态（product_crawl_queue 9 态实占 5 态）：success 1,439 · access_restricted 19,356 · fetch_failed 146 · parse_failed 37 · **pending（未采集/待处理）232,081**；合计 253,059
- 分类状态（product_classification）：movie 909 · tv_series 55 · collection 7 · concert 3 · other 11 · **pending_review（待确认）252,074**；合计 253,059
- 尚未完成者均显式记为未采集（pending）/待确认（pending_review），无静默丢失。

## 2. 评论有效性 / 异常 / 重复

| 项目 | 数量 | 口径 |
|------|------|------|
| 有效评论（原始记录全保留） | 7,911,684 | 隔离 0；movies.txt.gz SHA-256 `4b6ee09e…cdcc715` 不变 |
| 重复副本 | 5,146 | 全 8 字段逐字符一致才判重（unique 7,901,972 + primary 4,566） |
| **逻辑评论** | **7,906,538** | 重复副本去重后计 1 次（评分分母） |
| 判为不同记录（保留） | 14,419 对 | 候选同键但任一字段不同 → 不同记录 |
| 跨版本相似评论（仅统计不删） | 7,240,056 条 / 1,239,387 组 | 不同 ASIN 同文，原始全保留 |
| 异常 | **43 条**（helpfulness 分子>分母） | review_anomaly 共 86 行 = 两轮运行各记一次；0 负票/0 空用户/0 分数越界 |

## 3. 产品类别数量

| 类别 | 数量 | 说明 |
|------|------|------|
| 电影产品（movie） | 909 | evidence 891 + inferred 18 |
| 非电影产品 | 76 | tv_series 55 · collection 7 · concert 3 · other 11 |
| 待确认（pending_review） | 252,074 | 含未采集 232,081 + 受限 19,356 + 其余受限/证据不足（不猜） |

## 4. 合并前后作品与人物数量

| 域 | 合并前 | 合并后 |
|----|--------|--------|
| 作品（movie） | 909 个产品/版本（editions） | **656 作品实体** = merged 簇 159 + 单例 493 + 合集待定成员 4 |
| 人物（person） | 8,976 人名节点（IMDb nconst + 页面署名） | **8,319 人物实体** = merged 657 + 单例 7,662 |
| 角色关系 movie_person | — | 11,053 行（director/actor/actress，同人同片多角色多行） |
| 人工并集 | 43 对人工裁定合并 | 32 组 / 87 asin，链一致性 0 拒边；`manual_merge_preserved` 校验 32/32 OK |

## 5. 分字段覆盖率（缺失/推断/已核实分别统计）

**口径**：电影域分母 = 本轮已识别电影作品 **632 实体**（field_assertion 覆盖对象）；另报全产品识别覆盖率 = **985 / 253,059 = 0.389%**（分类≠pending_review 的产品）。

| 字段 | 覆盖实体 | 覆盖率 | 来源与状态三分 |
|------|----------|--------|----------------|
| title | 632/632 | 100% | selected 691 行（product_page + imdb）/ alias candidate 862 行保留 |
| genres | 516/632 | 81.6% | selected 1,271（work_genre + seller_category_tag 双角色） |
| directors | 464/632 | 73.4% | selected 797 · candidate 33 · conflict 21 |
| release_year | 462/632 | 73.1% | **已核实 459**（verified，precision=year）/ **冲突保留 482 行候选**（不挑一个）/ missing 173 |
| starring | 454/632 | 71.8% | selected 5,408 · candidate 94 · conflict 83 |

**上映/发售语义三分**（release_facts，run_etl_demo_02）：
| 事实 | 已核实 | 推断 | 缺失 |
|------|--------|------|------|
| release_year | 459 | 0 | 173（no_imdb_match / 候选年份冲突） |
| edition_sale_date（asin 级，永不入 release_year） | 507 / 909 产品 | 0 | 402（页面无日期字段） |
| earliest_review_time | 628 | — | 4（合集待定成员无单品评论） |
| inferred_release_bound（条件性上界） | — | **628（status=inferred, conf 0.70）** | — |

## 6. 约束 / 映射一致性 / 可追溯

- **quality_check 10/10 全过（violations=0）**：queue_total · classification_total · review_mapping_total（7,911,684）· fk_orphan×4（product_movie/release_facts/field_assertion/movie_person 均 0）· no_duplicate_product_movie（dup_keys=0）· classification_mutex_sum（253,059）· **manual_merge_preserved（人工组 32 个，违例 无）**
- 主键唯一性：review_mapping(asin,record_ordinal) 7,911,684 行无重复；product_movie(asin,movie_id) 无重复键；movie_entity/person_id 唯一。
- **非空最终字段可追溯**：lineage_field_final 4,415 条中 **4,238 条带 selected_assertion_uids**（可溯至 lineage_field_assertion → lineage_source_record 1,059 条源记录：评论文件/页面快照哈希/IMDb 数据集/人工复核）；**177 条为 conflict/空候选形态**，decision_reason 明示"conflict 保留（候选集合见断言行，不编造）"，非空最终字段均可追溯或有人工修订标记（manual_review 标记 10 条）。

## 7. 人工复核覆盖率与待确认率（单独报告）

- 人工复核覆盖：43 对人工合并裁定 + 2 对人物反例裁定（Wachowski/Errol Flynn）+ 10 对 IMDb 匹配样本复核 = **55 次人工判定**，全部入 `*_decision`/`evidence.manual_review`/`report/人工复核记录.md`。
- 待确认率：电影 **151 / 570 = 26.5%**（最新决策语义：auto+manual_merge 248+37、manual_review 151、no_merge 134）；人物 **378 / 842 = 44.9%**（run_20261104-04 口径，含缩写封顶与稀疏证据保守转入）。
- 评价集人工复核覆盖率：电影 100 对 + 人物 100 对已交付，**待人工标注**。

## 8. 实体匹配评价样本状态

- 电影：`logs/summary/merge_eval_sample.csv`（100 对：12 边界 auto + manual + no_merge 分层，seed=42，开发/评价分离）
- 人物：`logs/summary/person_eval_sample.csv`（100 对：边界 auto + 缩写封顶 + 同名反例分层）
- **Precision/Recall/F1 待人工标注后计算**（分母为零时记 n/a）；按作业口径，指标仅评价所构造样本，不宣称代表全量准确率。误合并/漏合并实例已备（Wachowski 反例、Metropolis 链合并、Dracula/Scrooge 别名组）。

## 9. 采集终态与证据（评分不按抓取页数排名）

| 指标 | 数值 |
|------|------|
| 已尝试 ASIN | 20,978 |
| 真实商品页 success | 1,439（成功率 6.86%） |
| 受限（验证页证据留档，永不标成功） | 19,356（92.2%） |
| fetch_failed / parse_failed | 146 / 37 |
| 未采集/待处理 | 232,081 |
| 采集证据链 crawl_attempt | 48,511 行（run_id+attempt_no+快照哈希） |

## 10. 口径与已知局限

1. 字段覆盖率为 run_etl_demo_02 转换时点口径（632 实体对象）；跨轮行数以 run_id 隔离，未合并计数。
2. 采集按用户指令于 wave 035 后暂停；232,081 未采集状态如实显示，不以小样本冒充全量。
3. P/R/F1 依赖 200 对人工标注，标注完成后按公式补算；当前不宣称准确率数字。
4. 幂等/恢复/规则版本证据见 `etl_run_task`（run_id/try_number/起止时间，16 行）与 §10 演示记录（中断恢复、37 簇→32 簇修复）。
