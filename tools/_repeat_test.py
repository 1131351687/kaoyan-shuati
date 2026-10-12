# -*- coding: utf-8 -*-
"""_repeat_test.py —— 测两个通道的「可重复性」：同题同提示词重复 N 次，看分数抖动。
这是判断能不能用某个通道做难度评估的关键指标：
  若同题重复抖动 > 档位宽度的一半，则该通道的分数不可信。"""
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB
import llm_difficulty as LD

REPEAT = 4
NQ = 8

c = sqlite3.connect(BANK_DB)
c.row_factory = sqlite3.Row
pool = LD.fetch_pending(c, type("A", (), {
    "reeval": True, "groups": None, "types": None, "limit": 0})())
random.seed(11)
rows = random.sample(pool, NQ)
prompt = LD.build_prompt(rows)
qids = [str(r["qid"]) for r in rows]

print("题目 %d 道，每通道重复 %d 次（temperature=0，同提示词）\n" % (NQ, REPEAT))

summary = {}
for ch in ("deepseek", "agnes"):
    LD.use_channel(ch)
    runs = []
    t0 = time.time()
    for i in range(REPEAT):
        try:
            g = LD.parse_scores(LD.call_llm(prompt))
        except Exception as e:
            print("  %s 第 %d 次失败: %s" % (ch, i + 1, e))
            g = {}
        runs.append(g)
    dt = time.time() - t0
    print("── %s  用时 %.0fs ──" % (ch, dt))

    spreads, levels = [], []
    print("  %-15s %s" % ("id", "  ".join("第%d次" % (i + 1) for i in range(REPEAT))))
    for q in qids:
        vals = [r.get(q, {}).get("diff") for r in runs]
        lv = [r.get(q, {}).get("level", "—") for r in runs]
        got = [v for v in vals if v is not None]
        sp = (max(got) - min(got)) if got else None
        if sp is not None:
            spreads.append(sp)
            levels.append(lv)
        print("  %-15s %s   极差 %s" % (
            q, "  ".join("%5s" % (("%.2f" % v) if v is not None else "  —") for v in vals),
            ("%.2f" % sp) if sp is not None else "—"))

    if spreads:
        nlv = sum(1 for lv in levels if len(set(lv)) == 1)
        print("  平均极差 %.3f   最大 %.3f   档位完全稳定 %d/%d\n" % (
            sum(spreads) / len(spreads), max(spreads), nlv, len(spreads)))
        summary[ch] = (sum(spreads) / len(spreads), max(spreads), nlv)

print("══ 结论 ══")
for ch, (avg, mx, nlv) in summary.items():
    verdict = "可信" if avg <= 0.08 else ("勉强" if avg <= 0.15 else "不可信")
    print("  %-9s 平均极差 %.3f  最大 %.3f  档位稳定 %d/%d  → %s" % (
        ch, avg, mx, nlv, NQ, verdict))
