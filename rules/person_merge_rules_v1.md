# 人物姓名归一与消歧规则 person_merge_rules

- **规则版本**：v1.1（文件名按要求 `person_merge_rules_v1.md`）
- **生效日期**：2026-11-04（v1.1 经人工审核生效）
- **适用范围**：任务5 人物实体合并（person_id 生成；movie_person 角色建模）
- **状态**：**生效**（v1.1 阈值冻结；按审核指令直接执行，不另跑预估）
- **上游依据**：`report/字段覆盖矩阵.md`（person 域来源标签）、`rules/merge_rules_v1.md` v1.3（任务4 电影合并，提供 movie_id/tconst 锚点）、`report/人工复核记录.md`
- **执行脚本**：`src/merge_persons.py`（幂等；DDL `sql/05_person_merge.sql`）

---

## 1. 实体与标识

| 概念 | 定义 | 标识 |
|------|------|------|
| **人物（person）** | 自然人（演员/导演等创作人员） | `person_id`（系统生成，格式 `p_<6位序号>`；**不用姓名、不用 nconst 充当**） |
| **外部 ID** | IMDb `nconst` 等来源定位符 | 存 `person_entity.external_ids`；同一 nconst 即同一人（已核实外部 ID 优先） |
| **姓名记录** | 原始姓名（原文不改意）、规范姓名、别名集 | `person_alias`（每条带 source + source_locator） |
| **参演/任职** | 人物-作品-角色 | `movie_person`（movie_id + person_id + role + ordering） |

**person_entity 关键列**：person_id、name_canonical（规范姓名）、external_ids JSONB（nconst 列表）、primary_occupation、era_span（作品年代区间）、status(merged/singleton/pending_review)、rules_version、run_id。

## 2. 范围与数据源

| 源 | 取数 | 来源标签 |
|----|------|----------|
| IMDb name.basics | nconst、primaryName、birthYear/deathYear、primaryProfession、knownForTitles | `imdb`（nconst 定位） |
| IMDb title.principals / title.crew | 人物×作品×角色（category、ordering、job） | `imdb`（tconst+nconst 定位） |
| 产品页贡献者署名 | 姓名原文 + (Director)/(Actor) 角色 | `product_page`（asin+快照哈希） |
| 任务4 产物 | movie_id ↔ tconst 映射（movie_entity.external_ids） | `system` |

**范围限定**：出现在（a）已识别电影作品（movie_entity）的演职员表；（b）产品页贡献者署名的人物记录。tv_series/concert 等其他类别的演职员留后续版本。

## 3. 姓名规范化（**仅用于候选生成，绝不作为合并依据**）

规范形 `name_canon`（两侧对称）：
1. NFKD 归一、变音符号剥离（Müller→Mueller 级）、大小写折叠、标点归一
2. 词序还原：`Last, First Middle` → `First Middle Last`
3. 后缀提取（Jr./Sr./II/III 独立字段，不入 canon）
4. **中间名处理**：全名、缩写、省略三种形态**全部**入候选键（`John A. Smith` / `J. A. Smith` / `John Smith` 互为候选）；删除中间名的结果**只做阻塞键**
5. **缩写**：`J. Smith` ↔ `John Smith` 生成候选，**缩写兼容永不直接合并**（判定上限 manual_review）
6. **音译/转写**：有限等价表（ä→ae、ö→oe、ü→ue、ß→ss；García↔Garcia；Wade-Giles↔拼音弱等价如 Chow↔Zhou 仅作候选）；音译等价**须别名或 nconst 佐证**才能作合并证据
7. **跨语言名称**：IMDb primaryName（拉丁转写）与产品页原文名**并列保存**，原文不改意；多写法经别名表归拢

**红线（作业要求）**：**姓名相同 ≠ 同一人**；**缩写相同不直接合并**；中间名删除/大小写统一/音译折叠**只产生候选对**。

## 4. 候选生成（阻塞键）

- K1：`name_canon` 完全相同（含别名 canon）
- K2：`姓 + 名首字母` 相同（抓中间名/缩写变体）
- K3：共享同一 `nconst`
- K4：音译等价 canon
- K5：共享作品（同一 movie_id/tconst 共同出现，且角色相容）

## 5. 验证信号（多信号联合）

每信号取值：`match=1.0` / `compatible=0.5` / `unknown=0` / `conflict=-1`；另有**硬冲突**（任一即否决 auto）。

| # | 信号 | match | compatible | conflict（软） | 硬冲突 | 来源 |
|---|------|-------|-----------|---------------|--------|------|
| S1 | 姓名层级 | name_canon/别名链相等；音译等价**且**有别名佐证 | 缩写兼容（仅首字母）；仅姓相同+名兼容 | 名不同且无别名链 | — | person_alias |
| S2 | 共同作品 | ≥2 部共同作品（同 movie_id 或同 tconst） | 1 部共同 | 0 共同但双方各 ≥2 独占作品（弱） | **互斥代表作**：双方各 ≥2 独占作品、交集 0 且同期活跃 | movie_person / principals |
| S3 | 职业 | 职业/角色相容（actor↔actress；导演↔导演） | 跨职能（actor↔director，可能编导演） | 职业互斥且各自明确（如一方纯导演、一方纯演员且 0 共同作品） | — | primaryProfession / category |
| S4 | 年代 | 生卒年/作品活跃区间重叠 | 一侧缺失；区间相邻（间隙 ≤5 年） | 区间间隙 >5 年**且双方已知作品各 <5 部**（数据稀疏，强制 manual_review，reason=`era_gap_sparse`） | **间隙 >5 年且双方各 ≥5 部已知作品**；或 deathYear < 对方首作年份 | birthYear/deathYear / 作品 startYear 区间 |

**年代口径（v1.1 补充）**：生卒年缺失时**仅用作品 startYear 推算**活跃区间（min~max），证据标 `era_basis='work_years'`、`status='inferred'` + 置信度（作品数 ≥5 记 0.8，3–4 记 0.6，≤2 记 0.4）；生卒年可得时 `era_basis='lifespan'`（evidence）。**数据稀疏 ≠ 年代不重叠**：任一侧已知作品 <5 部时，年代间隙不作硬冲突。
| S5 | 外部 ID | **同一 nconst** | — | — | 不同 nconst 且双方均为已核实独立 nconst 并满足 S2 互斥 | name.basics |

## 6. 置信度计算

- 验证分（仅对 known 信号，unknown 剔出分母）：
  `V = Σ(w_i × s_i) / Σw_i`，权重：S2 共同作品 0.40 · S4 年代 0.30 · S3 职业 0.15 · S1 姓名 0.15（S5 不进公式，走直通规则）
- 姓名系数 T：name_canon/别名链相等 1.0 · 音译等价+佐证 0.9 · **缩写兼容 0.5**（数学上封顶 manual）
- **置信度** `conf = T × (0.30 + 0.70 × max(V, 0))`

## 7. 三级判定逻辑

| 判定 | 条件（须全部满足） | 后续动作 |
|------|--------------------|----------|
| **auto_merge** | **①同一 nconst（已核实外部 ID，直通）**；或 ②无硬冲突 + T ≥ 0.9（完全同名/别名链）+ **S2 ≥1 共同作品** + V ≥ 0.70 + known ≥ 2 | 写 PersonDecision(accept, auto)；参与 §8 链合并；分配 person_id |
| **manual_review** | 无硬冲突，且（0.45 ≤ conf < 0.70；或 **缩写兼容（任意情形）**；或 0 共同作品但年代/职业相容；或音译等价无佐证；或 known < 2；或候选多对并列；或 **年代间隙 >5 年但任一侧作品 <5 部**（reason=`era_gap_sparse`）；或链不一致波及） | 入人工队列（附全信号证据）；**不合并** |
| **no_merge** | ①任一硬冲突；或 ②conf < 0.45；或 ③S1 conflict 且无别名链 | 写 PersonDecision(reject, reason)；保持独立 person_id |

**红线落点**：姓名相同但 0 共同作品 → 无法进 auto（须 S2≥1）；缩写兼容 → T=0.5 封顶 manual；**删除中间名/统一大小写的结果只出现在候选生成，不出现在判定证据**。

## 8. 同名/近似名不合并（反例判定）

| 情形 | 判定 | 硬规则 |
|------|------|--------|
| 同名同职业、同期活跃，但各自 ≥2 独占代表作、0 交集 | no_merge | S2 互斥代表作（H2） |
| 同名但活跃年代间隙 >5 年**且双方各 ≥5 部已知作品** | no_merge | S4 硬冲突（H1，v1.1 加作品数门槛） |
| 同名但年代间隙 >5 年、作品数任一 <5 | manual_review | `era_gap_sparse`（数据稀疏不判死） |
| 近似名（缩写/中间名差）且满足 H1/H2 证据 | no_merge | 同上 |
| 同名、同期、作品集可比但证据薄（各自作品 <2） | manual_review | 不猜 |

## 9. 链一致性（A≈B、B≈C 但 A≠C 不得合并）

与任务4 §7 同构：clique 约束 + 并查集按 conf 降序合并，合并前穷举跨簇对；存在 no_merge/硬冲突对 → 拒边改判 manual_review（reason=`chain_inconsistency`）；合并后内部全对复核、低分边拆簇；决策追加式（run_id 保留历史）。

## 10. MoviePerson 角色建模（作业要求）

- 表 `movie_person(movie_id, person_id, role, ordering, nconst, source, evidence)`：
  - `role ∈ {director, actor, actress}`（本版本范围；writer/producer 留扩展）
  - **同一人可有多角色**：同 (movie_id, person_id) 多行（如自导自演 → director + actor 两行）
  - `ordering` 取 IMDb principals ordering；产品页署名无序时 NULL
  - 来源标签：`imdb`（tconst+nconst 定位）/`product_page`（asin+快照哈希）；**产品页署名只作别名/消歧证据，正式角色以 nconst 为准**（不冒充）
- 角色不因合并丢失：合并后按 person_id 汇聚，原 nconst/来源定位保留在 evidence

## 11. 未确认处置（作业要求）

- 无法确认 → **保留多个 person 实体**并标 `status='pending_review'`，**避免误合并**；绝不为凑数合并
- 原始姓名原文全保留（raw 与 canonical/别名分列）；推断值标 `inferred` + confidence；决策不改写历史

## 12. 评价方案（作业要求：人物 ≥100 对人工标注）

- 分层抽样 100+ 对（auto_merge / manual_review / no_merge / 缩写与同名反例 各约 25），开发调参样本与评价样本分离（70/30，固定 seed=42）
- 以人工标注为金标准报告 auto_merge 的 Precision / Recall / F1；同名反例单独列正确率
- 阈值只在开发样本上调；评价集一次性评估

## 13. 落库设计预览（DDL 在执行步交付 `sql/05_person_merge.sql`）

| 表 | 作用 | 关键列 |
|----|------|--------|
| `person_entity` | 人物 | person_id(PK)、name_canonical、external_ids JSONB、primary_occupation、era_span、status |
| `person_alias` | 姓名/别名 | person_id、name_raw、name_canon、lang/script、source、source_locator |
| `person_merge_candidate` | 候选对+信号 | pair_id、两端 person 标识、signals JSONB、V、conf、known |
| `person_merge_decision` | 三级判定（追加式） | decision(auto_merge/manual_merge/manual_review/no_merge)、reason、rules_version、run_id |
| `movie_person` | 人物-作品-角色 | movie_id、person_id、role、ordering、nconst、source、evidence |
| `person_cluster_log` | 链合并审计 | run_id、成员、拒边、拆簇原因 |

## 14. 执行步交付物（审核通过后执行时输出）

1. **≥5 组人物合并实例**（附证据：共同作品/年代/职业/nconst）
2. **≥5 组同名/近似名不合并实例**（附证据：互斥代表作/年代不重叠等）
3. 合并前后数量统计（person_entity 簇数、movie_person 行数、pending_review 数）
4. 100 对评价样本（human_verdict 空列待标注）
5. `report/报告草稿.md` 6.2 节回填 + `ai_usage/ai_usage_log.md` 更新

## 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-11-04 | 初稿：person_id 生成与字段保存（原名/规范名/别名/nconst/证据）；中间名/缩写/音译/跨语言处理（仅候选生成）；S1–S5 验证信号（共同作品/职业/年代/外部 ID）；姓名相同≠同一人、缩写不直接合并红线；同名互斥代表作/年代不重叠硬冲突；pending_review 保留多实体；movie_person 多角色建模；置信度公式与三级判定阈值；链一致性；100 对评价方案；5+5 实例交付物 |
| v1.1 | 2026-11-04 | 按审核意见修订并生效：①年代硬冲突加作品数门槛——间隙 >5 年**且双方各 ≥5 部已知作品**才硬冲突，作品数不足降 manual_review（`era_gap_sparse`，数据稀疏≠年代不重叠）；②年代口径补充——生卒年缺失时仅用作品 startYear 推算，标 `era_basis='work_years'`+inferred+置信度。权重、auto 门槛、manual 区间/缩写封顶、互斥代表作、deathYear 矛盾、角色范围均维持 v1.0。按指令免预估直接执行 |
