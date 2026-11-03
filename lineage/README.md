# lineage/ — 血缘表与查询脚本

**放什么**：字段级血缘相关查询 SQL/脚本（4 个必答查询）、血缘表维护脚本、血缘实例导出（演示用）。

**对应作业**：九、字段级数据血缘（任务9）：
1. 输入 movie_id + 字段名 → 最终值、采用/未采用候选、原始证据、选择理由
2. 输入 ASIN 或 source_review_id → 版本、作品、匹配状态
3. 输入源记录 ID → 受影响最终实体与字段
4. 输入 person_id → 原始署名、别名归并过程、参与作品

**当前状态**：待 FieldAssertion/MergeDecision/FieldSelection 表（sql/）建好后开发查询。
