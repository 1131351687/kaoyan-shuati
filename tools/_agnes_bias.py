# -*- coding: utf-8 -*-
"""_agnes_bias.py —— 量化 agnes 相对 deepseek 的系统性偏差（更大样本）。"""
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB
import llm_difficulty as LD

N = 60
c = sqlite3.connect(BANK_DB)
c.row_factory = sqlite3.Row
pool = LD.fetch_pending(c, type("A", (), {
    "reeval": True, "groups": None, "types": None, "limit": 0})())
random.seed(7)
rows = random.sample(pool, min(N, len(pool)))
print("样本 %d 题（全库本地题中随机）\n" % len(rows))

out = {}
for ch in ("agnes", "deepseek"):
    LD.use_channel(ch)
    t0 = time.time()
    got = {}
    for i in range(0, len(rows), 10):
        chunk = rows[i:i + 10]
        try:
            got.update(LD.parse_scores(LD.call_llm(LD.build_prompt(chunk))))
        except Exception as e:
            print("  %s 批次失败: %s" % (ch, e))
    out[ch] = got
    print("  %-9s 取回 %2d/%d  %.0fs" % (ch, len(got), len(rows), time.time() - t0))

both = [r["qid"] for r in rows
        if str(r["qid"]) in out["agnes"] and str(r["qid"]) in out["deepseek"]]
print("\n可比 %d 题" % len(both))

A = [out["agnes"][k]["diff"] for k in both]
D = [out["deepseek"][k]["diff"] for k in both]
ma, md = sum(A) / len(A), sum(D) / len(D)

print("\n── 总体 ──")
print("  agnes    均值 %.3f  中位 %.3f" % (ma, sorted(A)[len(A) // 2]))
print("  deepseek 均值 %.3f  中位 %.3f" % (md, sorted(D)[len(D) // 2]))
print("  **系统偏差 (agnes - deepseek) = %+.3f**" % (ma - md))
print("  平均绝对差 %.3f   最大 %.3f" % (
    sum(abs(a - d) for a, d in zip(A, D)) / len(A),
    max(abs(a - d) for a, d in zip(A, D))))

sa = (sum((x - ma) ** 2 for x in A) / len(A)) ** .5
sd = (sum((x - md) ** 2 for x in D) / len(D)) ** .5
r = sum((a - ma) * (d - md) for a, d in zip(A, D)) / (len(A) * sa * sd)
print("  Pearson r = %.3f" % r)

same = sum(1 for k in both if out["agnes"][k]["level"] == out["deepseek"][k]["level"])
print("  档位一致 %d/%d = %.0f%%" % (same, len(both), 100.0 * same / len(both)))

print("\n── 档位混淆（行=agnes，列=deepseek）──")
lv = ["easy", "mid", "hard"]
print("  %-6s %6s %6s %6s" % ("", *lv))
for a in lv:
    row = [sum(1 for k in both
               if out["agnes"][k]["level"] == a and out["deepseek"][k]["level"] == d)
           for d in lv]
    print("  %-6s %6d %6d %6d" % (a, *row))

print("\n── 偏差按题型 ──")
for t in ("选择题", "填空题", "解答题"):
    ks = [k for k in both if out["agnes"][k].get("_t") is None]
    src = {str(r["qid"]): r["qtype_name"] for r in rows}
    ks = [k for k in both if src.get(k) == t]
    if ks:
        b = sum(out["agnes"][k]["diff"] - out["deepseek"][k]["diff"] for k in ks) / len(ks)
        print("  %-6s n=%2d  偏差 %+.3f" % (t, len(ks), b))

print("\n── 偏差按年份段 ──")
src = {str(r["qid"]): r for r in rows}
for lo, hi, name in ((1987, 1999, "1987-1999"), (2000, 2008, "2000-2008"),
                     (2024, 2026, "模拟卷")):
    ks = [k for k in both
          if src[k]["year"] and lo <= int(src[k]["year"]) <= hi]
    if ks:
        b = sum(out["agnes"][k]["diff"] - out["deepseek"][k]["diff"] for k in ks) / len(ks)
        print("  %-10s n=%2d  偏差 %+.3f" % (name, len(ks), b))
