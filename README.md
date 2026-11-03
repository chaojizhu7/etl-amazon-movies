# Amazon 电影评论与多源电影信息集成 ETL

数据存储与管理课程 ETL 作业实现。目标：对 Stanford SNAP Amazon movie reviews 全量数据执行抽取、转换、装载，完成电影/人物实体合并、信息补全与字段级血缘追踪。

## 目录结构

| 目录 | 内容 | 对应作业章节 |
|------|------|--------------|
| `data/raw/` | 原始数据与下载元数据 | 二、数据来源；任务1 |
| `snapshots/` | 产品页面快照与哈希索引 | 任务2 产品页面采集 |
| `src/` | 抽取、清洗、匹配、爬虫代码 | 任务1/2/3/4/5/6/7/8 |
| `sql/` | 建表脚本、索引、字段字典 | 八、目标数据模型 |
| `etl/` | ETL 作业定义、依赖、参数 | 八、ETL 架构 |
| `rules/` | 合并/去重/选择规则（带版本号） | 任务4/5/7/8 |
| `logs/` | 执行日志与失败处置 | 十、可复现与恢复 |
| `lineage/` | 血缘表与查询脚本 | 九、字段级数据血缘 |
| `ai_usage/` | AI 使用记录 | 十一、提交内容 |
| `report/` | 报告与验收材料 | 十一、项目报告 |

## 版本标识约定

- **解析程序版本**：`parser_version`，当前 v0.1（见 `src/` 内脚本与 `data/raw/download_meta.json`）
- **规则版本**：`rules_version`，规则文件命名 `rules/<类别>_v<版本>.*`
- **run_id**：单次 ETL 运行标识，格式 `run_<YYYYMMDD>-<序号>`，写入 ETLRun 表与日志

## 提交信息规范

格式：

```
<type>(<scope>): <简述> [parser=vX.Y][rules=vX.Y][run_id=run_YYYYMMDD-NN]
```

- `type`：`init` 初始化 / `feat` 新功能 / `fix` 修复 / `data` 数据装载 / `rules` 规则变更 / `etl` 作业调整 / `docs` 文档 / `test` 测试
- `scope`：`repo` / `src` / `sql` / `etl` / `rules` / `lineage` / `report` 等
- 版本标签：涉及解析/规则/数据运行的提交**必须**带上对应版本号，便于追溯"解析程序版本、规则版本"；纯文档可省略
- 示例：
  - `init(repo): 初始化 ETL 项目骨架 [parser=v0.1][rules=v0.0]`
  - `feat(src): 评论流式解析器 v0.2 [parser=v0.2]`
  - `rules(rules): 电影合并规则 auto-merge 阈值调整 [rules=v0.3]`
  - `data(etl): 全量评论装载批次 [parser=v0.2][rules=v0.3][run_id=run_20261105-01]`

## 环境

见 `requirements.txt`（51 包锁定）与 `src/env_check.py` 自检脚本。数据库连接模板 `db.env.example`（真实口令存 `db.env`，不提交）。
