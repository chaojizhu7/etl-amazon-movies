# 合并数据变更操作规范（SOP）v1.0

> 生效日期：2026-11-06。触发背景：2026-11-06 清噪操作后 merge 重跑把 32 个人工合并组全部拆散
> （根因：重放 primary 选择逻辑缺陷，详见 `logs/summary/qa3_try16.log` 与修复记录）。
> 任何对合并数据（product_movie / movie_entity / movie_manual_group / movie_merge_decision /
> release_facts / movie_alias / movie_person / field_assertion）的修改，必须按本规范执行。

## 一、变更前：快照备份（强制）

对**每一个**将被修改的表做全表备份，表名带日期：

```sql
CREATE TABLE movie_manual_group_backup_<YYYYMMDD> AS SELECT * FROM movie_manual_group;
CREATE TABLE product_movie_backup_<YYYYMMDD>     AS SELECT * FROM product_movie;
CREATE TABLE movie_entity_backup_<YYYYMMDD>      AS SELECT * FROM movie_entity;
-- release_facts / movie_alias / movie_person / field_assertion 同理
```

备份表保留至核对通过并完成下一个稳定里程碑后才可清理；清理需人工确认。

## 二、变更中：最小面手术

1. 优先直写 SQL 做确定性迁移（按 asin / 主键逐行搬），**不用**整链 merge 重算验证修复；
2. 迁移撞 UNIQUE 键时禁止静默删行：值全同才删重复行；值不同保留双方（run_id 加 `#keep:<old_id>` 后缀）；
3. 人工组（movie_manual_group）成员必须整组同簇、簇 id 必须等于 canonical_movie_id；
4. 清噪/拆分对（movie_manual_split）成员必须不同簇。

## 三、变更后：核对（全部通过才可继续）

1. 32 组逐组核验：成员全同簇 + 簇 id = canonical + 成员数与变更前清单一致；
2. `quality_check.py` 的 `manual_merge_preserved` 项 PASS；
3. 目标表行数与备份对账（迁移不增删业务行，除明确记录的清噪拆分）；
4. 输出变更 SQL 清单供人工审查。

## 四、红线

- **禁止**在核对通过前对合并数据做第二轮修改；
- **禁止**用 merge 全链重跑作为修复手段（重算有放大风险，先诊断根因）；
- 决策表只追加不改历史；人工裁定行 reason 必须带 `manual_confirm:`.
