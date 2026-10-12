#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用站点真题「真实熟练率难度」校准 LLM 评分（单向验证）。

站点题有 mastery（=1-熟练率），是真实数据；本地题没有。若 LLM 在站点题上
的评分与站点真实难度相关，则可用它评本地题。

同时做「同题型内部」相关性 —— 因为站点 mastery 本身受题型影响很大
（解答题天然难），跨题型相关系数会虚高。
"""
import json
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB  # noqa: E402
from llm_difficulty import build_prompt, call_llm, parse_scores, band_of  # noqa: E402


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row
    # 只取站点题（有 mastery + diff_n 足够大）
    rows = [dict(r) for r in c.execute(
        "SELECT id AS qid, qtype_name, year, score, kp_name, stem, mastery, diff_n "
        "FROM question WHERE group_id IN ('8','9','10') AND mastery IS NOT NULL "
        "AND diff_n >= 200 AND stem != ''")]
    random.seed(7)
    rows = random.sample(rows, min(n, len(rows)))
    print("抽样 %d 道站点题（有真实 mastery，样本量>=200）\n" % len(rows))

    got = {}
    B = 8
    for i in range(0, len(rows), B):
        chunk = rows[i:i + B]
        try:
            got.update(parse_scores(call_llm(build_prompt(chunk))))
        except Exception as e:
            print("  批次失败:", e)

    xs, ys, recs = [], [], []
    for r in rows:
        v = got.get(str(r["qid"]))
        if not v:
            continue
        real = 1.0 - r["mastery"]          # 站点真实难度
        xs.append(v["diff"])
        ys.append(real)
        recs.append((r["qid"], r["qtype_name"], round(v["diff"], 2),
                     round(real, 2), r["diff_n"], v["reason"][:26]))

    print("对比 %d 题\n" % len(recs))
    print("%-8s %-6s %6s %8s %7s  %s" % ("qid", "题型", "LLM", "站点真实", "样本", "LLM 理由"))
    for t in sorted(recs, key=lambda z: z[2]):
        print("%-8s %-6s %6.2f %8.2f %7d  %s" % t)

    print("\n总体 Pearson r = %.3f" % pearson(xs, ys))
    for t in ("选择题", "填空题", "解答题"):
        sx = [v["diff"] for r, v in ((r, got[str(r["qid"])]) for r in rows
                                     if r["qtype_name"] == t and str(r["qid"]) in got)
              ]
        sy = [1.0 - r["mastery"] for r in rows
              if r["qtype_name"] == t and str(r["qid"]) in got]
        if len(sx) >= 3:
            print("  同题型 %s (n=%d) r = %.3f" % (t, len(sx), pearson(sx, sy)))

    with open(os.path.join(os.path.dirname(BANK_DB), "..", "docs",
                           "_calib.json"), "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
