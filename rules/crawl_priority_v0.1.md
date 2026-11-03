# 采集优先级规则 crawl_priority

- **规则版本**：v0.1
- **生效日期**：2026-11-03
- **适用范围**：任务2 产品页面采集队列 `product_crawl_queue`

## 规则内容

1. **排序键**：`review_count`（该 ASIN 在 movies.txt 中的评论条数）**降序**——评论多的产品优先采集（Top-Review 优先，覆盖最多评论的头部产品）。
2. **并列打破**：`review_count` 相同时按 `asin` 升序，保证排序确定性、可复现。
3. **priority 字段**：排序后的名次（1 = 最高优先级），排序键 = (-review_count, asin)。
4. **优先级分层**（priority_tier，用于统计与调度）：
   - `high`：review_count > 100
   - `medium`：10 ≤ review_count ≤ 100
   - `low`：review_count < 10
5. **批次切取**：按 priority 升序取前 N 个作为一批（batch_001 = priority 1–100）。
6. **状态机**（status 字段，互斥）：
   `pending` → `crawling` → `success` / `page_not_found` / `access_restricted` / `timeout` / `parse_failed` / `fetch_failed`；三次跨日重试仍受限 → `permanently_unreachable`。
   - HTTP 200 验证页**永不**标 `success`（判别规则见 product_spider.classify）。
   - 失败状态保留证据，不回退为 pending 以外的假状态。

## 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 | 2026-11-03 | 初版：review_count 降序 + asin 升序破并列；三级分层；状态机定义 |
