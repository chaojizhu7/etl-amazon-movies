# -*- coding: utf-8 -*-
"""Task 8: 评论清洗与重复识别（v2：状态机解析 + COPY 后建索引）。

去重目标声明（区分两类）：
  A. 同一条评论的重复记录：同一 ASIN+用户+时间+评分+文本 且 **全 8 字段逐字符一致**
     → 判为重复副本（逻辑评论计 1 次，副本不删、只映射）
  B. 用户在不同版本上的相似评论：不同 ASIN 上的相似/相同文本
     → **绝不因文本接近而删除**，原始记录全部保留（只统计，不判重）
候选生成：ASIN+用户+时间+评分+文本 → 候选对；核验：任何字段不同（哪怕 1 字符）→ 保留为不同记录。

Usage: .venv\\Scripts\\python.exe src\\clean_reviews.py [--run-id run_20261104-09]
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "logs", "summary")
MOVIES_TXT = os.path.join(ROOT, "data", "raw", "movies.txt.gz")
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")

# 记录字段顺序（movies.txt 固定顺序，状态机解析）
FIELDS = ["product/productId:", "review/userId:", "review/profileName:",
          "review/helpfulness:", "review/score:", "review/time:",
          "review/summary:", "review/text:"]

SCORE_RULES = {
    "去重口径": "仅『全 8 字段逐字符一致』的记录判为同一逻辑评论的重复副本（A 类）；"
                "候选键（ASIN+用户+时间+评分+文本）相同但任一字段不同（哪怕 1 字符）→ 保留为不同记录",
    "跨版本评论": "用户在不同 ASIN（不同版本/合集）上的相似评论属 B 类：不删、不并、原始全保留，"
                  "仅统计；绝不因文本接近而去重",
    "原始记录": "movies.txt.gz 原始记录全部保留不动（SHA-256 `4b6ee09e…cdcc715` 不变）；"
                "副本仅在 ReviewMapping 标 duplicate_copy，不删除",
    "评分计算（电影级）": "movie 级评分 = Σ score(逻辑评论) / N(逻辑评论)；"
                        "评论经 product_movie(relation_type='single') 汇聚；"
                        "合集产品评论留在合集产品，不拆分/不复制/不摊派给成员电影（防重复计数）",
    "评分计算（去重）": "分母 = 逻辑评论数（重复副本去重后计 1 次）；分子 = 对应逻辑评论 score 之和",
    "评分计算（helpfulness）": "helpfulness 不参与评分加权，仅做校验",
    "防重复计数": "同一逻辑评论只计 1 次（logical_id=hfull_key 去重）；"
                  "跨版本同文评论各计各的产品，不跨产品合并",
}


def main(run_id):
    t0 = time.time()
    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "08_review_mapping.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        conn.execute("DELETE FROM review_mapping")   # 派生映射全表重建（PK 全局，避免跨 run 冲突）
        conn.execute("DELETE FROM review_anomaly WHERE run_id=%s", (run_id,))
        conn.execute("DROP INDEX IF EXISTS idx_rri_h5")
        conn.execute("DROP INDEX IF EXISTS idx_rri_hf")
        conn.execute("DROP INDEX IF EXISTS idx_rri_utex")
        conn.execute("DELETE FROM review_record_index")

        cur = conn.cursor()
        n = bad_score = empty_user = helpful_bad = helpful_unp = 0
        anomaly_rows = []
        t_parse = time.time()
        cur.execute("SELECT count(*) FROM review_record_index")
        have = cur.fetchone()[0]
        if have == 7_911_684:
            print(f"staging 已就绪（{have} 行），跳过解析/COPY/索引", flush=True)
            n = have
        else:
            with gzip.open(MOVIES_TXT, "rt", encoding="utf-8", errors="replace") as f, \
                    cur.copy("COPY review_record_index (asin,record_ordinal,user_id,review_time,"
                                 "score,helpful_num,helpful_den,h5_key,hfull_key,h_utex) FROM STDIN") as copy:
                vals = [""] * 8
                idx = -1

                def flush():
                    nonlocal n, bad_score, empty_user, helpful_bad, helpful_unp, vals, idx
                    if idx < 0:
                        return
                    n += 1
                    asin = vals[0].strip()
                    user = vals[1].strip()
                    prof = vals[2].strip()
                    help_raw = vals[3].strip()
                    score_raw = vals[4].strip()
                    t_raw = vals[5].strip()
                    summ = vals[6].strip()
                    text = vals[7]
                    hn = hd = None
                    m = re.match(r"(\d+)/(\d+)$", help_raw)
                    if m:
                        hn, hd = int(m.group(1)), int(m.group(2))
                        if hn < 0 or hd < 0:
                            helpful_bad += 1
                            anomaly_rows.append((asin, n, "helpfulness_negative", help_raw, run_id))
                        elif hn > hd:
                            helpful_bad += 1
                            anomaly_rows.append((asin, n, "helpfulness_num_gt_den", help_raw, run_id))
                    else:
                        helpful_unp += 1
                        anomaly_rows.append((asin, n, "helpfulness_unparseable", help_raw, run_id))
                    score = None
                    try:
                        score = float(score_raw)
                        if not (1 <= score <= 5):
                            raise ValueError
                    except ValueError:
                        bad_score += 1
                        anomaly_rows.append((asin, n, "bad_score", score_raw, run_id))
                    if not user:
                        empty_user += 1
                        anomaly_rows.append((asin, n, "empty_user", user, run_id))
                    tt = int(t_raw) if t_raw.lstrip("-").isdigit() else None
                    b_asin, b_user = asin.encode(), user.encode()
                    b_t, b_s = t_raw.encode(), score_raw.encode()
                    b_text = text.encode("utf-8", "replace")
                    b5 = hashlib.blake2b(digest_size=8)
                    for x in (b_asin, b_user, b_t, b_s, b_text):
                        b5.update(x); b5.update(b"\x1f")
                    bfull = hashlib.blake2b(digest_size=8)
                    for x in (b_asin, b_user, prof.encode(), help_raw.encode(), b_s, b_t,
                              summ.encode("utf-8", "replace"), b_text):
                        bfull.update(x); bfull.update(b"\x1f")
                    but = hashlib.blake2b(digest_size=8)
                    but.update(b_user); but.update(b"\x1f"); but.update(b_text)
                    copy.write_row((asin, n, user or None, tt, score, hn, hd,
                                    b5.hexdigest(), bfull.hexdigest(), but.hexdigest()))
                    if n % 1000000 == 0:
                        print(f"  parsed {n:,} ... {round(time.time()-t_parse,1)}s", flush=True)
                    vals = [""] * 8
                    idx = -1

                for line in f:
                    key, sep, val = line.partition(":")
                    key += sep
                    if key == "product/productId:":
                        flush()
                        idx = 0
                        vals[0] = val.rstrip("\n")
                    elif idx >= 0 and idx + 1 < 8 and key == FIELDS[idx + 1]:
                        idx += 1
                        vals[idx] = val.rstrip("\n")
                    elif idx == 7:
                        vals[7] += line
                    # 其余情况（字段内冒号行）：partition 后 key 不匹配 → 归入当前字段
                    else:
                        if idx >= 0:
                            vals[idx] += line if line.endswith("\n") else line + "\n"
                flush()

        print(f"stream+copy done: {n:,} rows in {round(time.time()-t_parse,1)}s", flush=True)
        with conn.cursor() as ac:
            for row in anomaly_rows:
                ac.execute("INSERT INTO review_anomaly (asin,record_ordinal,anomaly_type,"
                           "raw_value,run_id) VALUES (%s,%s,%s,%s,%s)", row)
        t_idx = time.time()
        conn.execute("CREATE INDEX idx_rri_h5 ON review_record_index (h5_key)")
        conn.execute("CREATE INDEX idx_rri_hf ON review_record_index (hfull_key)")
        conn.execute("CREATE INDEX idx_rri_utex ON review_record_index (h_utex)")
        print(f"indexes built in {round(time.time()-t_idx,1)}s", flush=True)

        t_sql = time.time()
        cand = conn.execute(
            "SELECT count(*), coalesce(sum(c*(c-1)/2),0) FROM ("
            " SELECT h5_key, count(*) c FROM review_record_index GROUP BY 1 HAVING count(*)>1) t").fetchone()
        print(f"q1 done {round(time.time()-t_sql,1)}s", flush=True)
        dup = conn.execute(
            "SELECT count(*), coalesce(sum(c*(c-1)/2),0), coalesce(sum(c-1),0) FROM ("
            " SELECT hfull_key, count(*) c FROM review_record_index GROUP BY 1 HAVING count(*)>1) t").fetchone()
        print(f"q2 done {round(time.time()-t_sql,1)}s", flush=True)
        kept_diff = conn.execute(
            "SELECT count(*), coalesce(sum(c*(c-1)/2),0) FROM ("
            " SELECT h5_key, count(*) c FROM review_record_index"
            " GROUP BY 1 HAVING count(*)>1 AND count(DISTINCT hfull_key)>1) t").fetchone()
        print(f"q3 done {round(time.time()-t_sql,1)}s", flush=True)
        cross = conn.execute(
            "SELECT count(*), coalesce(sum(c),0) FROM ("
            " SELECT h_utex, count(*) c FROM review_record_index"
            " GROUP BY 1 HAVING count(*)>1 AND count(DISTINCT asin)>1) t").fetchone()
        print(f"q4 done {round(time.time()-t_sql,1)}s", flush=True)
        t_map = time.time()
        conn.execute("DROP INDEX IF EXISTS idx_rm_logical")
        conn.execute("""
            INSERT INTO review_mapping (logical_id, asin, record_ordinal, map_status, run_id)
            SELECT * FROM (
                SELECT i.hfull_key, i.asin, i.record_ordinal,
                       CASE WHEN d.cnt=1 THEN 'unique'
                            WHEN i.record_ordinal=d.first_ord THEN 'primary'
                            ELSE 'duplicate_copy' END,
                       %s AS run
                FROM review_record_index i
                JOIN (SELECT hfull_key, count(*) cnt, min(record_ordinal) first_ord
                      FROM review_record_index GROUP BY 1) d ON d.hfull_key=i.hfull_key
            ) t ORDER BY record_ordinal
            """, (run_id,))
        conn.execute("CREATE INDEX idx_rm_logical ON review_mapping (logical_id)")
        print(f"mapping insert in {round(time.time()-t_map,1)}s", flush=True)
        map_cnt = dict(conn.execute(
            "SELECT map_status, count(*) FROM review_mapping WHERE run_id=%s GROUP BY 1",
            (run_id,)).fetchall())
        conn.commit()
        total = conn.execute("SELECT count(*) FROM review_record_index").fetchone()[0]
        print(f"sql analytics in {round(time.time()-t_sql,1)}s", flush=True)

    stats = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "原始记录数": int(total),
        "候选对数": {"候选组数(h5键相同)": int(cand[0]), "候选对数(组内两两)": int(cand[1])},
        "确认重复数": {"重复组数(全字段一致)": int(dup[0]), "重复副本数(超出首个)": int(dup[2]),
                       "重复对数": int(dup[1])},
        "保留数": {"原始记录全部保留": int(total),
                   "候选中判为不同记录的组数": int(kept_diff[0]),
                   "候选中判为不同记录的对数": int(kept_diff[1]),
                   "跨版本相似评论(不删仅统计)": f"组数 {int(cross[0])} / 涉及记录 {int(cross[1])}"},
        "异常数": {"helpfulness 违例": helpful_bad, "helpfulness 不可解析": helpful_unp,
                   "score 越界/非数": bad_score, "空用户": empty_user,
                   "合计": len(anomaly_rows)},
        "review_mapping": {k: int(v) for k, v in map_cnt.items()},
        "评分计算规则": SCORE_RULES,
    }
    sp = os.path.join(OUT_DIR, "review_cleaning_stats.json")
    json.dump(stats, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("==== 评论清洗完成 ====")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"wrote {sp}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run_20261104-09")
    a = ap.parse_args()
    sys.exit(main(a.run_id))
