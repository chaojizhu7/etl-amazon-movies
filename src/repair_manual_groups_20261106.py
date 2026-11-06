# -*- coding: utf-8 -*-
"""手术重建 32 个人工合并簇（2026-11-06 清噪事故修复）。

原则（rules/merge_data_change_sop.md）：
- 只按 movie_manual_group 锚定搬运成员到 canonical_movie_id，不动其他成员；
- 撞 UNIQUE 键不静默删行：值全同才删重复行，值不同保留双方（run_id 加 '#keep:<old_id>'）；
- Dr. Strangelove(B00004CIQG)/基督山伯爵(B00007K08G) 保持独立实体（清噪结果）；
- 输出每条变更供人工审查。

用法：.venv\\Scripts\\python.exe src\\repair_manual_groups_20261106.py [--dry-run]
"""
import argparse
import sys

import psycopg

DB = dict(host="localhost", port=5432, dbname="etl_movie", user="postgres", password="postgres")

# 与 run_etl_local.DEPENDENT_MOVIE_TABLES 对齐：(表, 键列, 值列, asin 过滤列)
DEP = [
    ("release_facts", "asin,fact_type,run_id", "value_raw,value_norm,precision,source,status,missing_reason,confidence", "asin"),
    ("field_assertion", "field_name,value_norm,value_role,source,run_id", "value_raw,status,decision_basis,reject_reason,confidence", "source_locator"),
    ("movie_alias", "title_canon,source,source_locator", "title_raw", "source_locator"),
]
MOVIE_LEVEL = [
    ("movie_person", "person_id,role,nconst,source", "ordering,evidence,rules_version", None),
]
DENOISE_ASINS = {"B00004CIQG", "B00007K08G"}


def lossless_move(conn, table, key_cols, val_cols, from_id, to_id, asin_col, asins, dry):
    """把 from_id 下属于 asins 的行搬到 to_id；撞键值全同删重复；
    值不同：键含 run_id 的表（release_facts/field_assertion）加 '#keep:' 后缀保留双方，
    否则（movie_person/movie_alias 键不含 run_id）删游离侧并告警留痕。"""
    keys = [c.strip() for c in key_cols.split(",")]
    vals = [c.strip() for c in val_cols.split(",")]
    key_has_runid = "run_id" in keys
    if asin_col and asins:
        rows = conn.execute(
            f"SELECT id, {', '.join(keys)}, {', '.join(vals)} FROM {table} "
            f"WHERE movie_id=%s AND {asin_col} = ANY(%s)", (from_id, list(asins))).fetchall()
    else:
        rows = conn.execute(
            f"SELECT id, {', '.join(keys)}, {', '.join(vals)} FROM {table} "
            f"WHERE movie_id=%s", (from_id,)).fetchall()
    moved = dup = kept = 0
    for row in rows:
        rid, k, v = row[0], tuple(row[1:1+len(keys)]), tuple(row[1+len(keys):])
        hit = conn.execute(
            f"SELECT id, {', '.join(vals)} FROM {table} WHERE movie_id=%s AND "
            + " AND ".join(f"{c} IS NOT DISTINCT FROM %s" for c in keys),
            (to_id, *k)).fetchone()
        if hit is not None:
            if tuple(hit[1:]) == v:
                if not dry:
                    conn.execute(f"DELETE FROM {table} WHERE id=%s", (rid,))
                dup += 1
            elif key_has_runid:
                if not dry:
                    conn.execute(
                        f"UPDATE {table} SET run_id = run_id || '#keep:' || %s, movie_id=%s WHERE id=%s",
                        (from_id, to_id, rid))
                kept += 1
            else:
                print(f"     !! {table} 键冲突且键不含 run_id，删游离侧留痕: key={k} from={v} keep={tuple(hit[1:])}")
                if not dry:
                    conn.execute(f"DELETE FROM {table} WHERE id=%s", (rid,))
                dup += 1
        else:
            if not dry:
                conn.execute(f"UPDATE {table} SET movie_id=%s WHERE id=%s", (to_id, rid))
            moved += 1
    return moved, dup, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    dry = a.dry_run
    with psycopg.connect(**DB) as conn:
        groups = {}
        for cid, asin in conn.execute("SELECT canonical_movie_id, asin FROM movie_manual_group ORDER BY 1,2"):
            groups.setdefault(cid, set()).add(asin)
        print(f"movie_manual_group: {len(groups)} 组 / {sum(len(v) for v in groups.values())} asin")
        fixed = 0
        for cid, members in sorted(groups.items()):
            rows = conn.execute(
                "SELECT asin, movie_id FROM product_movie WHERE asin = ANY(%s) AND relation_type='single'",
                (list(members),)).fetchall()
            cur = {asin: mid for asin, mid in rows}
            missing = members - set(cur)
            if missing:
                print(f"  !! {cid}: 缺 single 行 asin {missing}（跳过，需人工确认）")
                continue
            strays = {}
            for asin, mid in cur.items():
                if mid != cid:
                    strays.setdefault(mid, set()).add(asin)
            if not strays:
                continue
            fixed += 1
            print(f"== {cid} 修复: {len(strays)} 个游离 id")
            for sid, s_asins in sorted(strays.items()):
                print(f"   {sid} -> {cid}  成员 {sorted(s_asins)}")
                if not dry:
                    conn.execute("UPDATE product_movie SET movie_id=%s WHERE asin = ANY(%s) AND movie_id=%s",
                                 (cid, list(s_asins), sid))
                for tbl, key_cols, val_cols, asin_col in DEP:
                    m, d, k = lossless_move(conn, tbl, key_cols, val_cols, sid, cid,
                                            asin_col, s_asins if asin_col else None, dry)
                    if m or d or k:
                        print(f"     {tbl}: moved={m} dup_del={d} conflict_keep={k}")
                left = conn.execute(
                    "SELECT count(*) FROM product_movie WHERE movie_id=%s", (sid,)).fetchone()[0]
                if left == 0:
                    for tbl, key_cols, val_cols, _ in MOVIE_LEVEL:
                        m, d, k = lossless_move(conn, tbl, key_cols, val_cols, sid, cid, None, None, dry)
                        if m or d or k:
                            print(f"     {tbl}(簇级): moved={m} dup_del={d} conflict_keep={k}")
                    for tbl, key_cols, val_cols, asin_col in DEP:
                        if asin_col:
                            m, d, k = lossless_move(conn, tbl, key_cols, val_cols, sid, cid,
                                                    asin_col, None, dry)  # asin_col 但 asins=None → 全量
                            # 注：上面 None 会走全量分支，覆盖 asin='' 簇级行
                            if m or d or k:
                                print(f"     {tbl}(残留全量): moved={m} dup_del={d} conflict_keep={k}")
                    if not dry:
                        conn.execute("DELETE FROM movie_entity WHERE movie_id=%s", (sid,))
                    print(f"     movie_entity({sid}) 删除")
            if not dry:
                conn.execute("UPDATE movie_entity SET status='merged' WHERE movie_id=%s", (cid,))
        # 清噪对保持独立（验证不动）
        for asin in sorted(DENOISE_ASINS):
            r = conn.execute("SELECT movie_id FROM product_movie WHERE asin=%s", (asin,)).fetchone()
            print(f"清噪锚 {asin} -> {r[0] if r else '??'}（保持独立）")
        if not dry:
            conn.commit()
        print("DONE", "(dry-run)" if dry else "(committed)")


if __name__ == "__main__":
    main()
