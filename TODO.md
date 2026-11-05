# TODO（窗口 2，2026-11-05 15:50 关机收尾更新）

> 交接快照：QA-2 全部完成；QA-3 幂等测试中断在 dedup。恢复先读 CONTEXT.md §10。

## 最高优先（恢复后先做）

1. **QA-3 测试二（幂等）续跑**：
   - run1：`.venv\Scripts\python.exe src\run_etl_local.py --run-id run_qa3_idem --resume`（用 Start-Process 脱离 shell 启动，防工具超时杀进程；dedup≈25–30 min）
   - run1 完成后记录核心表计数（movie_entity/product_movie/person_entity/movie_person/review_mapping 等）
   - run2：换 run_id（如 `run_qa3_idem2`）全量再跑一遍，比对记录数与关系数一致 → 写测试二报告
   - 已验证：五 task SKIP 幂等恢复路径 OK（extract/crawl/classify/merge/enrich）
2. **QA-3 测试一（固定验收样本）**：5 movie_id + 5 person_id + 5 ASIN 已选定（见会话记录），跑前后对比
3. **QA-3 测试三（中断恢复演示）**：模拟 task 失败 → 补跑 → 证不漏不重（可复用 dedup try#1/2 的真实中断案例）
4. **QA-3 测试四（规则版本变化）**：改一个阈值 → 重算受影响记录 → 定位变更影响范围
5. **QA-3 三份测试报告**：每份含执行命令、前后对比数据、结论

## QA-4 最终交付（QA-3 完成后）

1. 5 个可执行查询（作品版本 / 导演作品 / 缺失与推断上映 / 字段冲突 / 评论统计）+ 示例结果；查询 e 必须解释合集、重复评论、版本合并口径
2. 项目报告定稿（覆盖全部验收要点）+ 单独一节"人工复核覆盖率和确认率" + 7.x 补"预售、合集、跨版本评论单独讨论"
3. 复现说明 + AI 使用记录汇总表（报告撰写部分如实记"AI 辅助整理文字与汇总数据，内容结论基于人工复核记录与实测数据"）
4. 演示提纲（10–15 分钟）——**个人准备材料不进 git**（report/演示提纲.md、report/答辩材料* 已在 .gitignore）
5. git 最终提交 `final: QA-3/4 完成 + 报告定稿 [etl=v1.0]`（**等用户明确指示再提交**；交前先 pull）

## 防回归提醒

- **重跑 merge/classify 会冲掉 `logs/summary/merge_eval_sample.csv`、`person_eval_sample.csv` 的 human_verdict 标注**（2026-11-05 实测被覆盖，已从 HEAD 恢复）——重跑前备份，重跑后核对 verdicts=100；标注版只信提交 5645936/649fc94
- 长任务用 Start-Process 脱离 shell 跑，否则工具超时杀进程 → PG 孤儿后端（诊断三板斧见 MEMORY）
- 中文文件只用 read/edit/write 工具；psql 传中文必坏

## 挂起遗留（非本轮交付必需）

- 11 对同 tconst 待判；manual 队列（电影 151 对 + 人物 378 对 + 字段 conflict 396 行）
- 任务4 遗留（Soy leyenda→tt0780504、Les Misérables/Ben-Hur 消歧、报告 §6.1 回填）
- 窗口 1 wave 035 收尾 + not_crawled 标记（win1-scan）
