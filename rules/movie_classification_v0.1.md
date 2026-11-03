# 电影页面筛选分类规则 movie_classification

- **规则版本**：v0.1
- **生效日期**：2026-11-03
- **适用范围**：任务3 产品分类（`product_classification` 表；输入 = `product_crawl_queue` + 采集快照 + IMDb title.basics）
- **执行脚本**：`src/classify_products.py`（幂等，可随采集进度重跑）

## 1. 分类体系（7 类，互斥）

| 分类 | 操作性定义 |
|------|-----------|
| `movie` | 单一叙事性影视作品（院线片/电视电影/直发录像带电影）的一次发行。IMDb titleType ∈ {movie, tvMovie}，或页面证据指向单一正片 |
| `tv_series` | 电视剧集（整季/全系列/单集/迷你剧）。IMDb titleType ∈ {tvSeries, tvMiniSeries, tvEpisode, tvPilot}，或页面剧集证据 |
| `collection` | **一盘多片**：同一产品含 ≥2 部互不相同的影视作品（三部曲/合辑/box set）。产品-作品为多对多（ProductMovie 预留） |
| `concert` | 演唱会、音乐现场表演、巡演录像、音乐视频合辑 |
| `non_movie` | 非影视作品商品（游戏、书籍、硬件、配件、纯音乐 CD 等） |
| `other` | 影音制品但不属以上（教学/健身/瑜伽/卡拉OK/单口喜剧/短片等） |
| `pending_review` | **待确认**：无快照、访问受限、页面不可判、信号冲突 |

## 2. 证据信号（只从结构化位置提取，不做全页关键词扫描）

> 全页文本含大量导航/广告噪声（实测每页都有 "Live/DVD/4K" 等字样），**全页词频不作为证据**。

**A 级（可单独定类）**
- **A1 IMDb 匹配**：产品标题规范化后与 IMDb `title.basics`（primaryTitle/originalTitle）匹配，匹配层级见 §4；titleType 映射见 §5
- **A2 页面剧集标记**：产品标题含 `Season <N>` / `Complete Series` / `Episodes` / `Season <N>-<M>`，且碟数或聚合片长佐证（多碟或片长 > 240 分钟）
- **A3 页面合集证据**：产品标题含 `Trilogy`/`Collection`/`Box(ed) Set`/`Complete ... Collection`，或 **Genre 字段列出 ≥2 部具体影片名**，且聚合片长/碟数与多片一致

**B 级（支持/投票，不能单独定类）**
- B1 贡献者角色：页面 (Director)/(Actor) 标注
- B2 片长带：正片 70–180 分钟；剧集聚合 > 240 分钟；短内容 < 70 分钟
- B3 面包屑/Genre 类目：`Action & Adventure`/`Drama`/`Comedy`/`Kids & Family` 等→电影向；`Exercise & Fitness`/`Comedy/Stand-Up`→other 向；`TV`→剧集向；`Concerts & Music Videos`/`Music Videos & Concerts`→concert 向（注意：该类目实测有噪声，需其他信号佐证）
- B4 分级：PG/PG-13/R/G→院线片向；NR→不定
- B5 部门标签：`<title>` 尾缀（如 `: Movies & TV` / `Amazon.com Music`）
- B6 载体/包装：DVD/Blu-ray/VHS/HD DVD/4K/Steelbook/Gift Set/N-Disc/2-pack/Edition

## 3. 硬性禁止（R0/R1/R2）

- **R0 载体词零证据**：标题含 DVD/Blu-ray/VHS/4K/Format/Edition/Widescreen 等**单独不构成任何分类依据**（只是发行载体/版本）。
- **R1 单片多碟 ≠ 合集**：`N Disc`/`2-pack`/`Gift Set`/`Steelbook`/`Special Edition` 修饰**单一标题**时仍是单品（movie），不是 collection。判定 collection 必须能列出 ≥2 个互不相同的候选影片标题。
- **R2 不猜**：`access_restricted` / `fetch_failed` / `not_crawled` / 快照解析不出 A/B 级信号 → 一律 `pending_review`（pending_reason 区分），禁止外推。

## 4. IMDb 匹配（`data/raw/imdb/title.basics.tsv.gz`，source=imdb）

标题规范化顺序（每步产物都保留 raw）：
1. HTML 反转义；取 `productTitle` 为原始标题
2. 抽取别名候选：括号内疑似独立标题段（如 `(The Notebook)`、`aka El diario de Noa`）、`aka <title>` 段
3. 剥离发行/版本后缀：`[VHS]` `[Blu-ray]` `[HD DVD]` `- BD` `- DVD` `- Season N` `(Ultimate 2 Disc Edition)` `(Two-Disc ... Edition ...)` `(Steelbook Edition)` `(Special Edition ...)` `(Amazon/... Packaging/Blu-ray)` `(Blu-ray)` `(DVD)` `Eng Subs [YYYY]` `[NTSC/Region ...]` `Requires a Multi Region Player` `: The Movie` 尾缀 `Complete` 等（剥离表见脚本常量）；斜杠双标题（`Kingdom of Heaven/Crusaders`）拆分为独立变体
4. 比较用规范形：NFKD 归一（`Alien³`→`Alien 3`）、大小写不敏感、`&`→`and`、`vol./pt.` 展开、分隔符 `: - – — / ,` 等价为空格、字母-数字边界分词、冠词 the/a/an 全局剔除（含尾部倒装 `Christmas Story, A`；两侧对称）

匹配层级（记录于 evidence）：
- `exact_primary`：规范标题 = primaryTitle（规范形比较）
- `exact_original`：规范标题 = originalTitle
- `alias_exact`：别名候选 = primaryTitle/originalTitle
- 以上均为**精确匹配**（规范形比较）；不做模糊/编辑距离匹配（不许猜）

歧义处理：
- 唯一候选（规范形）→ `match_status=unique`
- 多候选同为一类（如全是 movie）→ `match_status=unique_class`，类别可用
- 多候选**跨类**（movie vs tvSeries…）→ `match_status=ambiguous`，依次消歧：
  1. **年份标记**：标题 `[YYYY]` 与唯一候选 `startYear` 一致 → 采纳该候选
  2. **演职员消歧**：页面署名（`<title>` 人名列表、(Director)/(Actor) 贡献者）与候选 IMDb `title.principals`/`title.crew`（nconst→name.basics 规范名）重名比对，**唯一最高且 ≥2 人命中** → 采纳该候选（记 evidence.cast_match）
  3. **最具体变体优先**：某规范化变体/别名单独精确命中 1 个非碰撞型候选（排除 tvEpisode/short/tvShort）→ 采纳
  4. **同名碰撞过滤**：候选中 tvEpisode/short/tvShort/tvPilot（无页面剧集信号时）与 video/videogame/tvSpecial（页面为视频载体且无演唱会/教学信号时）视为**同名碰撞噪声**，不参与类别竞争；过滤后类别唯一 → 按该类（inferred）
  5. 消歧失败 → 用 A2/A3/B 级投票；仍不足 → `pending_review`（reason=`imdb_ambiguous`）
- **弱匹配降级**：候选仅为 short 类且页面片长 ≥70 分钟（正片带）时，短片候选与页面证据矛盾 → 不采信其 other 类别，按 B 级投票判定
- 无候选 → `match_status=none`，仅可依据页面 A2/A3 定类

## 5. titleType → 分类映射（仅 A1 路径）

| titleType | 映射 |
|-----------|------|
| movie, tvMovie | movie |
| tvSeries, tvMiniSeries, tvEpisode, tvPilot | tv_series |
| videoGame | non_movie |
| short | other（短片/短内容，不混入正片 movie） |
| video | B 级投票：concert 信号→concert；正片信号→movie（inferred）；否则 pending_review |
| tvSpecial | B 级投票：音乐/演唱会信号→concert；单口喜剧/喜剧特辑→other；否则 pending_review |
| tvShort, 其他未列 | pending_review |

## 6. 判定流程（优先级从上到下，首个命中即定）

1. **P0 采集状态**：非 success 快照 → `pending_review`（reason ∈ {not_crawled, access_restricted, fetch_failed, page_unparsed}）
2. **P1 信号冲突**：A 级信号互相矛盾（如 A1 唯一 movie vs A2 剧集标记；B5 部门与影视信号矛盾且无 A 级）→ `pending_review`（reason=`signal_conflict`，冲突写入 evidence）
3. **P2 non_movie**：B5 非影视部门/类目明确，且无影视信号 → non_movie
4. **P3 collection**：A3 命中（列出 ≥2 个候选影片）→ collection
5. **P4 concert**：标题/描述含演唱会标记（`Live in Concert`/`In Concert`/`Live from` 等），或（`Concerts & Music Videos` 类目 **且** IMDb titleType ∈ {video, tvSpecial} 佐证）→ concert。**仅 genre 类目不足**（实测噪声大）
6. **P5 tv_series**：A2 命中 → tv_series
7. **P6 movie**：A1 映射 movie；或 IMDb 候选跨类含 movie 时 B 级投票 ≥2/4（B1 Director、B2 正片带片长、B3 电影类目、B4 院线分级）→ movie；或（无可用 A1 时）B2+B1+B3 → movie
8. **P7 other**：B3 明确（Exercise & Fitness / Comedy/Stand-Up / Yoga / Instructional / Karaoke）或标题明确（Workout 等）→ other
9. **P8 其余** → `pending_review`（reason=`evidence_insufficient` 或 `imdb_ambiguous`）

## 7. 判定状态与置信度

| classification_status | 含义 | confidence |
|----------------------|------|------------|
| `evidence` | A 级证据定类（IMDb unique/unique_class 一致，或 A2/A3 明确） | 0.8–0.9 |
| `inferred` | 仅 B 级组合推断（铁律4：标 inferred） | 0.4–0.7 |
| `pending` | pending_review | NULL |

- 双源一致（页面 + IMDb）→ confidence 上限 0.9；单源 A 级 → 0.8；B 级多信号一致 → 0.7；B 级单信号 → 0.4。
- **片长异常**（如判 movie 但 runtime > 240 分钟）：不改类别，evidence 记 `runtime_anomaly`，confidence 降一档（最低 0.4）。
- 推断值不得覆盖已核实值；本规则产物均为分类元数据，不回写评论与快照。

## 8. 评论保留（作业要求）

无电影映射的评论**全部保留**于源记录（movies.txt 源文件不动）；分类只决定 ProductMovie 映射候选，不删除、不改任何评论。非电影类产品的评论同样保留。

## 9. 输出与落库

- 表：`product_classification`（每 ASIN 一行，全量 253,059 覆盖）、`product_movie_candidate`（collection 的候选多电影映射，ProductMovie 多对多预留）
- 合集候选影片写 `product_movie_candidate`；movie/collection ASIN 清单写 `data/raw/asin_movies.txt`（供 run_scan_wave.py 优先扫描钩子）
- 统计写 `logs/summary/classification_stats.json`；run_id = `run_<YYYYMMDD>-<序号>`

## 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-11-03 | 初版：7 类体系；A/B 级证据分层；R0 载体词零证据、R1 单片多碟非合集、R2 不猜；IMDb 精确匹配三级与歧义处理；P0–P8 判定流程；inferred/confidence 定义 |
