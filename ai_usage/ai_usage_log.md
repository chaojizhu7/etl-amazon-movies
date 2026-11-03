# AI 使用记录汇总

> 按时间追加。每条来自对话回答末尾的"AI 使用记录"。
> AI 生成的值不作为事实来源；人工验证方式须逐条落实。

---

## 2026-11-03 · 环境搭建与仓库初始化

- **工具/模型**：MiMoCode（mimo agent）+ winget + psql + py -3 + curl + git
- **用途**：下载元数据脚本、Python venv 与依赖、PostgreSQL 17.11 安装与 etl_movie 建库、磁盘内存估算、环境自检脚本、git 仓库结构
- **被采纳的输出**：`src/` 5 个工具脚本；requirements.txt（51 包）；估算表（实测 4 项）；env_check 核对表；目录骨架与 .gitignore
- **人工验证方式**：运行 `src/env_check.py` 看核对表全 PASS；psql 连接 etl_movie；`git status` 确认大文件被忽略

## 2026-11-03 · 人工核验（用户执行）

- **人工验证方式**：①抽查 2 条记录字段全部一致；②git log 有提交；③git status 无大文件；④目录 10 个齐全
- **结果**：**全部通过**

## 2026-11-03 · 评论解析与统计核对

- **工具/模型**：MiMoCode（mimo agent）+ Python 3.14.7 venv
- **用途**：movies.txt.gz 全量流式解析，与官方统计核对
- **被采纳的输出**：`src/stream_review_stats.py`；实测 7,911,684 评论 / 253,059 ASIN / 889,176 用户 / 1997-08~2012-10 四项全部 MATCH
- **人工验证方式**：重跑统计脚本核对数字；Get-FileHash 比对 SHA-256；抽查报告 2.2 表格（用户已完成抽查，见上方核验记录）
