# -*- coding: utf-8 -*-
"""修复 v2：人工合并组金标准锚定（显式成员表 + 人工片名基准核对）。

背景：merge 重建回滚人工合并成果；v1 的"release_facts 多数标题"解析被 id 碰撞
噪声误导（4/32 组错解）。v2 改用**显式成员表**（来源=2026-11-04 人工裁定的
合并对 + product_classification 标题/tconst 双核验），固化 movie_manual_group；
union 与 canonical 回写由 merge 步骤重放（run_etl_local.task_merge）。

Usage: .venv\\Scripts\\python.exe src\\repair_manual_merges.py
"""
import json
import os
import sys
from datetime import datetime, timezone

import psycopg

DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")

# 32 人工组金标准（canonical_id, 成员 asin, 核验标题）——43 对合并闭包
# 28 组取自 run_20261104-08/06/10/03 裁定；4 组（* 标注）经 v1 错解修正后重核
GROUPS_V2 = [
    ("mv_000006", ["6302914485", "B00005NBBJ", "B00005TPCG", "B005FOW9SW", "B008UY8FI2"], "A Christmas Carol 1951 + Scrooge"),
    ("mv_000010", ["6304176325", "B00008G1WJ", "B0009FGWLW", "B0021L9MO6", "B005F96UF0"], "Willy Wonka & the Chocolate Factory"),
    ("mv_000012", ["6304214510", "B00004CTVX", "B000P0J0AG"], "Heat"),
    ("mv_000022", ["B00004COW2", "B00004RUKM", "B0000844MV", "B00008YNDC", "B004SB60LK", "B0064NTZ6W"], "Das Boot"),
    ("mv_000030", ["B00005B1YC", "B0013D8LAE", "B00004R780"], "Night of the Living Dead（* v1 错解修正）"),
    ("mv_000039", ["B000058CAQ", "B0000VAFO0", "B0000W5J0E", "B000TGJ80I"], "Underworld"),
    ("mv_000040", ["6305099014", "B000085RPG", "B00018D42W", "B0029O0BMC"], "Texas Chainsaw Massacre"),
    ("mv_000046", ["B000WGYMAQ", "B000E111YO", "B000LXH0AY", "B000E1YW0E"], "Rent（* v1 错解修正）"),
    ("mv_000071", ["B00004RLDX", "B00005V9IL", "B003C6JZ6E"], "The Abyss"),
    ("mv_000075", ["6301681363", "B00004CJJQ", "B00004CZ1R"], "Metropolis"),
    ("mv_000084", ["B0006Z2KIY", "B0036JQ7S6"], "The Notebook / El diario de Noa（* v1 错解修正）"),
    ("mv_000093", ["B00004RQCV", "B00005NVFN"], "Snow White and the Seven Dwarfs（* v1 错解修正）"),
    ("mv_000104", ["B00075H6CS", "B0007A2GSW"], "The Incredibles"),
    ("mv_000116", ["0792164946", "B00004RXO9"], "Braveheart 1995"),
    ("mv_000153", ["B00004SGMF", "B0000C24F3"], "Aliens"),
    ("mv_000160", ["B000VIRD6U", "B000Y7U98W"], "Sunshine"),
    ("mv_000164", ["B0013MYB9K", "B001O0EDJO"], "Ben-Hur"),
    ("mv_000173", ["B000069510", "B0002J4X1Q"], "Resident Evil"),
    ("mv_000193", ["B00063AO16", "B000W10FWU"], "Harry Potter and the Chamber of Secrets"),
    ("mv_000204", ["B002ZG98VE", "B006WXHJGC"], "Robin Hood 2010"),
    ("mv_000207", ["B00003CWTI", "B00004YVFW"], "Magnolia"),
    ("mv_000212", ["B00004CYNY", "B000TGJ7ZY"], "Dracula 1992 / Bram Stoker's Dracula"),
    ("mv_000223", ["B00004RTJK", "B000O76T86"], "Independence Day"),
    ("mv_000226", ["630268644X", "B000057QG7", "B0002476HO"], "Reservoir Dogs"),
    ("mv_000247", ["B00008YNI2", "B000Q2GD4Y"], "Irreversible"),
    ("mv_000256", ["B00013YQC4", "B001EH7H7W"], "The League of Extraordinary Gentlemen"),
    ("mv_000267", ["0790743213", "B00004CJN8"], "North by Northwest"),
    ("mv_000273", ["B00005NFXH", "B000TGJ8BW"], "White Christmas"),
    ("mv_000287", ["B0002C9D9K", "B0002C9D9U"], "Luther"),
    ("mv_000308", ["6303050239", "B00004CJ59", "B000MRK7AO"], "The Good, the Bad and the Ugly"),
    ("mv_000315", ["B00004CY8G", "B00407PNWO"], "The Adventures of Robin Hood"),
    ("mv_000326", ["B00004RKQU", "B00004RQ6C", "B00005N918"], "Total Recall"),
]


def main():
    with psycopg.connect(**DB) as conn:
        conn.execute(open(os.path.join(os.path.dirname(__file__), "..", "sql",
                                       "11_manual_group_anchor.sql"), encoding="utf-8").read())
        titles = {r[0]: (r[1] or "") for r in conn.execute(
            "SELECT asin, page_title_raw FROM product_classification")}
        conn.execute("DELETE FROM movie_manual_group")
        before = []
        for cid, asins, label in GROUPS_V2:
            missing = [a for a in asins if a not in titles]
            if missing:
                raise SystemExit(f"{cid}: asin 不在 product_classification: {missing}")
            for a in asins:
                conn.execute("INSERT INTO movie_manual_group (canonical_movie_id, asin) VALUES (%s,%s)",
                             (cid, a))
            mids = [r[0] for r in conn.execute(
                "SELECT movie_id FROM product_movie WHERE asin = ANY(%s) AND relation_type='single'",
                (asins,))]
            before.append({"canonical": cid, "label": label, "asins": len(asins),
                           "before_clusters": len(set(mids)),
                           "titles": sorted({titles[a][:44] for a in asins})})
        conn.commit()
    out = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "groups": len(GROUPS_V2), "asins": sum(len(a) for _, a, _ in GROUPS_V2),
           "before": before}
    p = os.path.join(os.path.dirname(__file__), "..", "logs", "summary", "manual_merge_repair.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    for b in before:
        print(f"  {b['canonical']} [{b['before_clusters']}簇→待并] {b['label'][:40]} | {b['titles']}")
    print(f"锚定表写入: {len(GROUPS_V2)} 组 / {sum(len(a) for _, a, _ in GROUPS_V2)} asin → movie_manual_group")
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
