# 备用方案 v0.2（修正版）：全量快速扫描 + 代表性精细采集 + IMDb 补全

> v0.1 → v0.2 修正（用户拍板 2026-11-03）：作业要求"每个产品均须有处理状态；无法取得的页面必须保留原因和证据"
> → **253,059 个 ASIN 全部要有'至少尝试过一次'的记录**，不能只采样。触发背景：10 ASIN 验证批 access_restricted=9≥8，30s 限速无效。
> 原则：如实记录、不绕验证、不伪造成功。**待用户确认后启动。**

## 1. 快速状态扫描（全量 253,059，覆盖保底）

**目的**：不是采数据，而是给每个产品留下"尝试过 + 结果 + 证据"的记录。

| 参数 | 值 | 说明 |
|------|-----|------|
| 范围 | 全部 253,059 ASIN 各 1 次请求 | 队列 status=pending 逐批推进 |
| 间隔 | 2~5s/请求（建议 **2s 起步**，拦截升级则升到 5s） | 不用 30s（那是精细采集的参数） |
| 单并发 | CONCURRENT_REQUESTS_PER_DOMAIN=1 | 保持合规 |
| 记录字段 | ASIN、HTTP 状态、响应大小、内容特征、时间戳、run_id、attempt_no | 落 `crawl_attempt` 表 |
| 快照策略 | success → **完整快照+SHA-256**；access_restricted → 验证页快照（3,781B 特征）；page_not_found/timeout/error → 只记状态不存快照 | 证据最小化存储 |
| 恢复 | 波次断点续跑：每波取 pending 前 N 个，失败不回退 pending | 幂等 |

**内容特征判别**（实测可靠）：验证页 = 3,781B + captcha 标记；商品页 = productTitle/dp 特征；其他如实标 `other`。

**存储预估**【样本推算】：受限快照 ~227K × 3.7KB ≈ 0.85GB；成功快照 ~25K × ~300KB ≈ 7.2GB；合计 ~8GB。

### 扫描耗时（实测数字推算）

| 间隔 | 总耗时（24h 不间断） | 备注 |
|------|---------------------|------|
| **2s** | 253,059 × 2s ≈ **5.9 天** | 建议起步值；与 6 天工期基本匹配 |
| 3s | ≈ 8.8 天 | 用户原估算口径 |
| 5s | ≈ 14.6 天 | 拦截升级时降速用 |

（耗时 = 请求数 × 间隔；抓取传输耗时已含在间隔节奏内。扫描 24h 挂后台，与精细采集/IMDb 匹配并行。）

## 2. 代表性精细采集（叠加，产出字段级数据）

| 参数 | 值 |
|------|-----|
| 数量 | 20~30 ASIN/天，单波 |
| 间隔 | 30s±抖动 |
| 优先 | **movie 产品优先**（IMDb 匹配出的电影产品队列），其次 high 层 |
| 产出 | 完整快照+哈希，供任务3 电影筛选/任务5 演职员抽取用 |
| 重试 | 精细采集失败者进入 T+1/T+3/T+7 跨日重试 |

与扫描的关系：扫描=全覆盖保底（状态+证据）；精细=字段深度（少量高质量快照）。同一 ASIN 两次记录都进 `crawl_attempt`（attempt_no 区分）。

## 3. IMDb 补全（不变）

作品域字段（title/release_year/genres/directors/starring）← IMDb 5 表匹配（tconst 校验、年份±1/导演/类型联合验证）；
edition 域无产品页 → NULL + `missing_reason=page_unreachable`；来源标签严格分离（`imdb` vs `product_page`）。
完整上映日期 → NULL + `imdb_provides_year_only`。

## 4. 终态与统计口径

- **每个 ASIN 必有终态**：success / access_restricted / page_not_found / timeout / parse_failed / fetch_failed / permanently_unreachable（三轮重试后）/ 未采集（工期截止时仍在队列中的，作业允许该状态）
- **互斥状态数之和 = 253,059**（质量核对必查项）
- `crawl_attempt` 为证据链：可查"谁、何时、第几次、结果、证据路径"；"至少尝试过一次" = count(distinct asin) = 253,059
- 受限证据 = 验证页快照（3,781B 特征可判别），**永不标 success**

## 5. Airflow DAG 编排方案

### 5.1 DAG 一览

| DAG | 调度 | 任务链 | 职责 |
|-----|------|--------|------|
| `fast_scan_dag` | 每 2h 一波（可连续触发） | pick_batch → scan_batch → record_results → wave_checkpoint | 全量扫描推进（每波 ~2400 ASIN @2s ≈ 80min） |
| `fine_collect_dag` | 每日 1 波 | pick_movie_sample → crawl_fine → save_snapshots → update_status | 代表性精细采集 20~30/天 |
| `retry_dag` | 每日 1 次 | pick_due_retries → crawl_fine → update_status | T+1/T+3/T+7 跨日重试 |
| `imdb_match_dag` | 每日 1 次（或手动） | extract_titles → match_tconst → apply_fields → write_lineage | IMDb 匹配与字段补全 |
| `quality_dag` | 每日 1 次 | status_distribution → integrity_checks → report_snapshot | 状态分布、一致性核对（含"之和=253,059"） |

### 5.2 fast_scan_dag 任务图（核心）

```
pick_batch (SQL: pending ORDER BY priority LIMIT 2400)
    → scan_batch (BashOperator → scrapy product_scan / python scanner)
        → record_results (upsert crawl_attempt + update product_crawl_queue.status)
            → wave_checkpoint (写 logs/summary/scan_wave_<run_id>.json)
```

- **依赖关系**：线性链；`record_results` 失败不重跑 `scan_batch`（快照已落盘，只重放落库）
- **参数**：`batch_size`、`delay`、`run_id=scan_<rules_version>_<date>`（conf 可传）
- **失败分支**：`on_failure_callback` 写 QualityIssue 表 + 日志；波次级重试（整波重放幂等）
- **幂等**：`crawl_attempt` 以 (asin, run_id, attempt_no) 唯一；重复执行 INSERT ON CONFLICT DO NOTHING；queue 状态只前进不回退

### 5.3 数据模型增补

`sql/02_crawl_attempt.sql`：crawl_attempt 表（asin、run_id、attempt_no、http_status、response_bytes、content_feature、evidence_path、content_sha256、attempted_at）——"每个产品至少尝试过一次"的核验依据。

### 5.4 断点恢复

- 波次中断：queue 中 `crawling` 状态超时 30min 自动回退 `pending`（恢复任务）
- 同输入重跑：ON CONFLICT 保证不重复装载；attempt 记录不重复
- 规则变更：新 run_id，保留旧 attempt 历史

## 6. 时间线与预计总耗时

| 工作流 | 耗时 | 并行性 |
|--------|------|--------|
| 全量快速扫描 | **5.9 天**（2s 起步，24h 后台）；2~5s 区间 = 5.9~14.6 天 | 与下面全部并行 |
| 精细采集 | 10~15 分钟/天 × 14 天 | 并行 |
| IMDb 匹配补全 | 开发 0.5 天 + 运行 ~0.5 天 | 并行（离线） |
| 报告/评价/血缘 | 持续回填 | 并行 |

**关键路径 = 快速扫描 5.9~8.8 天**。若工期为 6 天：2s 起步可基本扫完（7×24h 挂机）；扫不完的尾部以 `未采集/待处理` 终态收尾（作业明确允许该状态，"尚未完成者必须显示为未采集或待处理"）。

## 7. 风险与边界

1. 拦截升级（90% 受限实测）：扫描以"留证"为主要目的，不追求成功率；验证页快照即证据
2. IP 彻底封禁（连验证页都拿不到）：状态记 `fetch_failed`/`timeout`，同样满足"有处理状态"
3. 存储 ~8GB：磁盘余量充足（161GiB）
4. 合规红线不变：robots.txt 遵守、不绕验证、不登录、不破封禁
