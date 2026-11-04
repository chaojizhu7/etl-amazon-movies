# 电影实体合并规则 merge_rules

- **规则版本**：v1.3（文件名按要求 `merge_rules_v1.md`）
- **生效日期**：2026-11-04（v1.2 经人工确认生效）
- **适用范围**：任务4 电影实体合并（movie_id 生成；仅 `product_classification.classification='movie'` 的产品 + 合集成员映射）
- **状态**：**生效**（v1.2 阈值冻结；调参须升版本）
- **上游依据**：`rules/movie_classification_v0.1.md`（分类）、`report/人工复核记录.md`（错配/未消歧标记）、`report/字段覆盖矩阵.md`（movie_id=system 生成、来源标签规范）
- **执行脚本**：`src/merge_movies.py`（幂等；DDL `sql/04_movie_merge.sql`）

---

## 1. 术语与实体边界

| 概念 | 定义 | 标识 |
|------|------|------|
| **作品（movie）** | 同一次主创生产（同一拍摄素材/同一剧本拍摄工程）的电影 | `movie_id`（系统生成，格式 `mv_<6位序号>`，**不用 ASIN、不用 tconst**） |
| **版本（edition）** | 作品的一次发行/一个商品载体（DVD/BD/VHS/语言版/地区版/剪辑版/礼盒） | `edition_id` = ASIN（`product_crawl_queue`/`product_classification` 主键） |
| **外部 ID** | IMDb `tconst` 等，作来源定位符存 `movie_entity.external_ids` + SourceRecord | tconst 永不充当 movie_id |
| **映射** | 产品-作品关系（单品 1:1；合集 N:M） | `product_movie` |

**任务边界**：任务4 只合并 `movie` 类（含 tvMovie→movie 的作品）；`tv_series`/`concert`/`other`/`non_movie`/`pending_review` **不进入** movie 合并。合集产品本身不是作品，只产生 `product_movie` 多对多映射。人物消歧（导演/主演的同名合并）归任务5；本规则仅使用 IMDb nconst 与"名字归一后完全相同"做人物比对，任务5 可对结果追溯修正。

## 2. "同一电影"操作性定义

**判定测试（核心）**：两发行是否指向**同一主创生产**（同一拍摄素材、同一导演/编剧工程下的同一部成片）——与载体、语言、字幕、地区、剪辑、修复无关。

### 2.1 归并为同一 movie_id（各自保留 edition_id）

| 情形 | 示例（本项目实测数据） | 判定 |
|------|------------------------|------|
| 不同载体/格式 | 《Back to the Future Trilogy》BD 版 B00198X0UO 内含的 Part I 与 DVD 版 Part I | 同 movie_id |
| 不同语言/字幕/地区 | 《The Notebook》B0006Z2KIY ≡ "aka El diario de Noa" 西语版 B0036JQ7S6（tt0332280） | 同 movie_id |
| 别名/译名发行 | "SOY LEYENDA - BD" ≡ I Am Legend 2007（tt0780504，人工复核确认） | 同 movie_id |
| 导演剪辑/加长/未分级/修复/重制 | 《Watchmen》剧场版 B001TJV0M8（163min）≡ 导演剪辑 B002BNKQDK（187min），同 tt0409459 | 同 movie_id，不同 edition |
| 包装变体（礼盒/铁盒/双碟/2-pack） | Iron Man "Ultimate 2 Disc Edition" B00005JPS8 ≡ 普通版；Polar Express Gift Set B000AM6MKU ≡ B000I2JKCO | 同 movie_id |

### 2.2 分别建模（绝不合并）

| 情形 | 示例 | 判定 |
|------|------|------|
| 重拍片 | Ben-Hur 1959（tt0052618）≠ Ben-Hur 2016（tt2638144） | 不同 movie_id |
| 续集/前传/番外 | Back to the Future Part I / II / III | 三个 movie_id（合集只做多对多映射） |
| 同名不同作品 | A Christmas Carol 1951（tt0044008，Sim 版）≠ 2009 动画版等同名改编 | 不同 movie_id |
| 原作与翻拍（真人/动画各自成片） | 同故事不同拍摄工程 | 不同 movie_id |
| 剧集/单集/演唱会/教学片 | 任务3 已分流，不入本合并 | 不同实体域 |

**版本词 vs 作品词**：`Director's Cut / Extended / Unrated / Remastered / Restored / Special Edition / N-Disc / Gift Set` 是**版本词**（同作品）；`2 / 3 / II / Part N / Sequel 序数` 是**作品词**（续集，分开）；`Remake/重拍` 由验证信号（年份+导演+主演）识别，不靠标题词。

## 3. 候选生成（候选对）

1. **标准化标题**：复用任务3 规范形（NFKD、`: - – — / ,` 等价空格、vol./pt. 展开、字母-数字边界、冠词全局剔除），得到 `title_canon`。
2. **别名收集**（每条带来源定位）：
   - 产品页原文标题（product_page + snapshot_sha256）
   - 标题内 `aka X`、括号内疑似独立标题（如 `(The Notebook)`、`(Men Who Hate Women)`）
   - IMDb primaryTitle/originalTitle（tconst 定位，source=imdb）
   - 人工复核修正（`evidence.manual_review`，如 Soy leyenda→tt0780504）**作为已确认别名**
   - 别名表 `movie_alias(title_raw, title_canon, source, source_locator)`，原文保留不改意
3. **阻塞键（blocking，避免全量两两）**：
   - B1：`title_canon` 完全相同（含别名两两之间）
   - B2：`title_canon` 去序数后缀（` 2`/` ii`/` part 2` 等剥离）相同 **且** 两侧序数标记一致（否则是续集对，直接进 no_merge 队列不进验证）
   - B3：同一 tconst 命中的所有 edition（任务3 的 candidate/匹配 + 人工修正）
   - B4：合集候选成员标题（`product_movie_candidate.movie_title_raw` 规范化后）与单品标题
4. **候选对类型**：edition×edition、edition×tconst、tconst×tconst（后两者用于给 movie_id 锚定外部 ID）。

## 4. 验证信号（多信号联合）

每信号取值：`match=1.0` / `compatible=0.5` / `unknown=0`（不计入分母）/ `conflict=-1`（软冲突）；另有**硬冲突**（任一即否决）。

| # | 信号 | match | compatible | conflict（软） | 硬冲突 | 数据来源 |
|---|------|-------|-----------|---------------|--------|----------|
| S1 | 标题 | title_canon 相等 / 别名链相等 | 词面 Jaccard ≥ 0.85 或仅版本词差异 | Jaccard < 0.5 且无别名链 | 标题序数标记不一致（`2` vs `3`，续集） | movie_alias / 产品页 / IMDb |
| S2 | 年份 | 相同（precision=year） | \|Δ\| ≤ 3 | 4 ≤ \|Δ\| ≤ 9；**\|Δ\| ≥ 10 且标题高度相似**（见下注） | \|Δ\| ≥ 10 **且标题关键词不重合**（重拍信号） | imdb.startYear（source=imdb）；产品页"Year"仅作辅助不单独用 |
| S3 | 导演 | 同一 nconst 或名字归一相同 | 一方缺失/合作导演含对方 | 双方有导演且不同（无其他强信号时） | 不同导演 **且** S4 主演 0 重合（重拍模式） | title.crew→name.basics / 产品页 (Director) 署名 |
| S4 | 主演 | ≥2 共同主演（nconst 或名字归一；IMDb ordering≤10） | 1 人共同 | 0 重合但一方主演表不全 | 0 重合 **且** 双方主演表均 ≥3 人可比 | title.principals / 产品页 (Actor) 署名 |
| S5 | 片长 | \|Δ\| ≤ 10min 或 ≤ 10% | ≤ 40%（须无矛盾信号；剪辑版差可至此） | > 40% 且无剪辑/合集解释 | 比值 > 1.6 且一侧有多片标记（合辑 vs 单片） | imdb.runtimeMinutes / 产品页 Runtime |
| S6 | 类型 | Jaccard ≥ 0.5（IMDb genres ∪ 产品页 genre） | 交集非空但 < 0.5；或一侧缺失 | 交集为空且双方各 ≥2 标签（**软冲突不单独否决**） | — | imdb.genres / 产品页 Genre |

**S2 年份两档（v1.1 审核修订）**：
- \|Δ年\| ≥ 10 且**标题关键词不重合**（内容词 Jaccard < 0.5 且无别名链）→ **硬冲突**（重拍/不同作品，no_merge）；
- \|Δ年\| ≥ 10 但**标题高度相似**（内容词 Jaccard ≥ 0.85，或仅版本词差异，或别名链相等）→ **不作硬冲突**，S2 记 conflict(-1)，该对**强制 manual_review**（reason=`year_gap_title_similar`，可能是修复/重制版，须人工看成片判定）；
- 0.5 ≤ Jaccard < 0.85 的中间情形：S2 记 conflict(-1)，按常规打分判定（不触发硬冲突）。

**规则红线（作业要求）**：**单独标题相似**或**单独年份相同**不足以 auto_merge——见 §6 判定门槛（要求 ≥2 个验证信号达 match 级）。

## 5. 置信度计算

- 验证分（仅对 known 信号，unknown 剔出分母）：
  `V = Σ(w_i × s_i) / Σw_i`，权重：S2 年份 0.30 · S3 导演 0.28 · S4 主演 0.22 · S5 片长 0.12 · S6 类型 0.08（Σ=1.00）
- 标题系数 T：别名/规范形相等 1.0 · 仅版本词差异 0.85 · 高词面相似（Jaccard ≥ 0.85）0.7 · 词面中等（0.5 ≤ Jaccard < 0.85）0.6（弱标题，数学上不可能达 auto_merge 门槛）
- **置信度** `conf = T × (0.30 + 0.70 × max(V, 0))`（软冲突把 V 拉低；硬冲突不进公式直接否决）
- 有已知 unknown 信号时记录 `known_signals = n`（判定对 n 有下限要求）

## 6. 三级判定逻辑

```
输入候选对 → 提取 6 信号 → 硬冲突? → 判定
```

| 判定 | 条件（须全部满足） | 后续动作 |
|------|--------------------|----------|
| **auto_merge** | ①无硬冲突；②T ≥ 0.85；③V ≥ 0.65；④**S2/S3/S4 中 ≥2 个 match 级**；⑤known_signals ≥ 3 | 写 MergeDecision(accept, auto)；参与 §7 链合并；分配 movie_id |
| **manual_review** | 无硬冲突，且（0.50 ≤ conf < 0.85；或 V ≥ 0.65 但强信号 <2；或 known_signals < 3；或候选多对并列；或携带任务3 标记 `version_unverified`/`tconst_unverified`；或 **S2 年份两档的 `year_gap_title_similar` 情形**；或 **S1 title_conflict 但 V ≥ 0.80**（reason=`title_conflict_high_v`，疑同片别名如 Dracula ≡ Bram Stoker's Dracula，人工判）；或 §7 链不一致波及） | 入人工队列（附全信号证据）；**不合并**，等人判 |
| **no_merge** | ①任一硬冲突；或 ②conf < 0.50；或 ③S1 判 conflict 且无别名链**且 V < 0.80** | 写 MergeDecision(reject, reason)；保持独立 movie_id 候选 |

- 阈值冻结于本版本；调参必须升版本并重跑全量判定（§10 评价集约束）。
- **阈值校准流程（v1.1）**：执行前先跑合并预估报告（auto/manual/no_merge 对数）；若 manual_review > 50 对，则放宽 auto_merge 的验证门槛 **V ≥ 0.55 或 known_signals ≥ 2**（在原③⑤上二选一放宽，①②④与硬冲突不动），预估报告确认后冻结执行阈值并在变更记录注明。
- 任务3 遗留标记的确定性处置：`wrong_match`（Soy leyenda）按其 `expected_tconst` 重建别名链后参与验证（修正本身待人工确认，确认前该对至多 manual_review）；`no_match`（Spark of Insanity）不造候选对。

## 7. 合并链一致性（A≈B、B≈C 但 A≠C 不得合并）

**不变量**：一个 movie_id 簇内任意两成员必须两两 auto_merge 级一致（clique 约束）。

**算法（确定性、可复现）**：
1. 取全部 auto_merge 候选边，按 `(conf 降序, 对端标识升序)` 排序（固定 tie-break）。
2. 并查集逐边合并；合并 C1、C2 前**穷举跨簇对** (u∈C1, v∈C2)：
   - 全部为 auto_merge 级（或补验后通过）→ 允许合并；
   - 存在 no_merge/硬冲突对 → **拒收本边**，该边与波及的跨簇对改判 `manual_review`，reason=`chain_inconsistency`；
   - 存在未验证对 → 现场补验；补验不可得（证据不足）→ 拒收本边并改判 manual_review（reason=`chain_unverified`）。
3. 合并完成后对每个簇做**内部全对复核**；任一对失败则按 (conf 最低边优先) 拆簇，拆出节点改判 manual_review。
4. 冲突边全部记录于 MergeDecision（含冲突簇快照），规则变更重跑时按 run_id 追加新决策、不改写历史（"规则变化保留决策版本"）。

## 8. ProductMovie 多对多与评论归属

- **合集**（任务3 `collection` + `product_movie_candidate`）：一部合集 N 个成员作品 → `product_movie` N 行（`relation_type='collection_member'`，`match_basis`=genre 列表/标题/候选映射证据）。
- **单品**：`relation_type='single'`（1 个 ASIN 对 1 个 movie_id）。
- **评论归属（红线）**：评论行永远挂在 product/edition（ASIN）上——**不拆分、不复制到每部电影、不按成员数摊派**。电影级统计一律通过 `review → product_movie → movie_id` 连接聚合（视图/查询层实现），保证源评论 7,911,684 条一行不多一行不少。
- 合集内成员的 movie_id 分配走单成员验证（候选来源= `product_movie_candidate.movie_title_raw`），合集本身**不生成 movie_id**。

## 9. 落库设计预览（DDL 在执行步交付 `sql/04_movie_merge.sql`）

| 表 | 作用 | 关键列 |
|----|------|--------|
| `movie_entity` | 作品 | movie_id(PK)、title_canonical、external_ids JSONB（tconst 列表）、status |
| `movie_alias` | 别名 | movie_id、title_raw、title_canon、source(review/product_page/imdb/manual)、source_locator |
| `movie_merge_candidate` | 候选对+信号 | pair_id、asin/tconst 两端、signals JSONB、V、conf、known_signals |
| `movie_merge_decision` | 三级判定（追加式） | pair_id、decision(auto_merge/manual_review/no_merge)、reason、rules_version、run_id、decided_at |
| `product_movie` | 产品-作品映射 | asin、movie_id、relation_type(single/collection_member)、match_basis、evidence |
| `movie_merge_cluster_log` | 链合并审计 | run_id、cluster 成员、被拒边、拆簇原因 |

- 全部写入幂等（ON CONFLICT + run_id 追加）；edition→ASIN 已在 `product_classification`，不重建。

## 10. 评价方案（作业要求：电影合并 ≥100 对人工标注）

- **样本**：分层抽样 100+ 对（auto_merge / manual_review / no_merge / 链边界 各约 25），开发调参样本与最终评价样本**分离**（70/30 固定 seed，写入 run 记录）。
- **指标**：以人工标注为金标准，报告 auto_merge 的 Precision / Recall / F1（另报 no_merge 召回）；冲突样本（重拍/续集/同名）单独列正确率。
- 阈值只允许在开发样本上调；评价样本一次性评估，结果写报告 §6.1。

## 11. 已知边界与局限

1. title.akas 未下载 → 别名仅来自产品页原文 + 人工复核；跨语种别名覆盖有限（如 Soy leyenda 类需人工/任务4 标注补链）。
2. 片长剪辑差可至 ~40%（剧场 vs 导演剪辑），S5 阈值据此放宽；极端修复版（4K 修复 4h+）可能落 manual_review（宁严勿滥）。
3. 人物比对先用 nconst/名字归一；任务5 合并 PersonAlias 后需重跑受影响候选对（决策表按 run_id 可追溯）。
4. 产品页无演职员/年份的稀疏页（任务3 实测约 1/3）候选对多落 manual_review——这是如实状态，不放宽标题单信号门槛。

## 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-11-03 | 初稿：同一电影操作性定义（版本/语言/剪辑归一，重拍/续集/同名分离）；候选生成与别名链；6 信号验证（标题相似/年份单独不足以 auto_merge）；置信度公式与三级判定阈值；链一致性 clique 算法；ProductMovie 评论不复制红线；落库预览与 100 对评价方案 |
| v1.1 | 2026-11-03 | 按审核意见修订：①S2 年份硬冲突两档化（≥10 且标题关键词不重合=硬冲突；≥10 但标题高度相似=强制 manual_review，疑修复/重制版）；②新增阈值校准流程（预估 manual>50 对则放宽 V≥0.55 或 known≥2，硬冲突不动）；其余（权重、auto 门槛、manual 区间、其余 3 条硬冲突、片长 40%、movie_id 格式）维持 v1.0 |
| v1.2 | 2026-11-04 | 按人工确认修订并生效：S1 title_conflict 且 V ≥ 0.80 → 降为 manual_review（reason=`title_conflict_high_v`，疑同片别名，人工判定），不再直接 no_merge；V < 0.80 维持 no_merge。预估样本中 Dracula ≡ Bram Stoker's Dracula、Scrooge ≡ A Christmas Carol 等 3 组转人工队列 |
| v1.3 | 2026-11-04 | 执行后校验发现缺陷，按铁律3（来源不冒充）修订：①验证信号只采信**已消歧/唯一候选** tconst 的作品证据，或成对**唯一共享** tconst 锚定证据；禁止跨候选作品并集演职员（同名异片污染）；②未消歧多候选节点的候选对**不进 auto_merge**（ceiling → manual_review，reason=`ambiguous_node_ceiling`）。阈值与其余判定逻辑不变 |
