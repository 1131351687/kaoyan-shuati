# -*- coding: utf-8 -*-
"""_agnes_stability.py —— 加大重复次数复核 agnes 稳定性，排除偶发。
对同一批题重复 6 次，看平均极差与档位稳定率；并把 3 次结果取中位数，
对比「单次 agnes」与「3 次取中位数」相对 deepseek 的误差——若中位数能救回来，
则 agnes 仍可用（代价是 3 倍耗时）。"""
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB
import llm_difficulty as LD

REPEAT = 6
NQ = 8

c = sqlite3.connect(BANK_DB)
c.row_factory = sqlite3.Row
pool = LD.fetch_pending(c, type("A", (), {
    "reeval": True, "groups": None, "types": None, "limit": 0})())
random.seed(11)
rows = random.sample(pool, NQ)
prompt = LD.build_prompt(rows)
qids = [str(r["qid"]) for r in rows]

runs = {}
for ch in ("agnes", "deepseek"):
    LD.use_channel(ch)
    rs = []
    t0 = time.time()
    for i in range(REPEAT):
        try:
            rs.append(LD.parse_scores(LD.call_llm(prompt)))
        except Exception as e:
            print("  %s #%d 失败: %s" % (ch, i + 1, e))
            rs.append({})
    runs[ch] = rs
    print("%-9s %d 次用时 %.0fs" % (ch, REPEAT, time.time() - t0))

print("\n── agnes %d 次 ──" % REPEAT)
sp_all = []
for q in qids:
    v = [r.get(q, {}).get("diff") for r in runs["agnes"]]
    g = [x for x in v if x is not None]
    lv = {r.get(q, {}).get("level") for r in runs["agnes"]}
    if g:
        sp_all.append(max(g) - min(g))
        print("  %-15s %s  极差 %.2f  档位数 %d %s" % (
            q, " ".join("%5.2f" % x for x in v), max(g) - min(g), len(lv),
            "稳定" if len(lv) == 1 else "漂移"))
print("  平均极差 %.3f  最大 %.3f" % (sp_all and sum(sp_all) / len(sp_all) or 0,
                                      max(sp_all) if sp_all else 0))


def med(vals):
    v = sorted(x for x in vals if x is not None)
    return v[len(v) // 2] if v else None


print("\n── 单次 vs 取中位数（相对 deepseek 一次评分）──")
print("  %-15s %8s %8s %8s %8s" % ("id", "deepseek", "agnes单次", "agnes中位", "中位误差"))
for q in qids:
    dv = runs["deepseek"][0].get(q, {}).get("diff")
    a1 = runs["agnes"][0].get(q, {}).get("diff")
    am = med([r.get(q, {}).get("diff") for r in runs["agnes"]])
    if None not in (dv, a1, am):
        print("  %-15s %8.2f %8.2f %8.2f %8.2f" % (q, dv, a1, am, abs(am - dv)))
