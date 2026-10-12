#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 难度 vs 旧先验公式：对撞评估。

核心问题（见用户 m02101 背景）：旧先验公式把 599 道本地题全部压在 mid 区间
（选择 0.39–0.51 / 填空 0.49–0.61 / 解答 0.62–0.74，都够不到 hard 的 0.70），
导致 hard 档只能从站点题里挑。

本脚本抽样本地题，同时算「旧先验」与「LLM 评分」，对比：
1. 两法的相关性（若高度一致 → 换 LLM 无意义）
2. 各档分布（LLM 能否覆盖面 wider）
3. 分歧最大的题（看 LLM 判断是否更合理）

不写库，纯评估。
"""
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB  # noqa: E402
from llm_difficulty import build_prompt, call_llm, parse_scores, band_of  # noqa: E402

# 旧先验公式（与 app/index.html / app/bank.py 的 qHardness 一致）
TYPE_PRIOR = {"选择题": 0.55, "填空题": 0.45, "解答题": 0.32}
BANDS = ((0.38, "easy"), (0.70, "mid"), (1.01, "hard"))


def prior_hardness(qtype_name, year, score):
    p = 1.0 - TYPE_PRIOR.get(qtype_name, 0.5)
    try:
        y = int(year)
        p += (2012 - y) * 0.005
    except (TypeError, ValueError):
        pass
    try:
        s = float(score)
        if s >= 12:
            p += 0.04
        elif s <= 8:
            p -= 0.03
    except (TypeError, ValueError):
        pass
    return max(0.0, min(1.0, p))


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT id AS qid, qtype_name, year, score, kp_name, stem, group_id "
        "FROM question WHERE (group_id LIKE 'old%' OR group_id LIKE 'mock%') "
        "AND stem != ''")]
    random.seed(11)
    rows = random.sample(rows, min(n, len(rows)))
    print("抽样 %d 道本地题（LLM vs 旧先验）\n" % len(rows))

    got = {}
    for i in range(0, len(rows), 8):
        try:
            got.update(parse_scores(call_llm(build_prompt(rows[i:i + 8]))))
        except Exception as e:
            print("  批次失败:", e)

    recs = []
    for r in rows:
        v = got.get(str(r["qid"]))
        if not v:
            continue
        pr = prior_hardness(r["qtype_name"], r["year"], r["score"])
        recs.append({"qid": r["qid"], "type": r["qtype_name"], "year": r["year"],
                     "score": r["score"], "llm": v["diff"], "prior": round(pr, 2),
                     "llm_band": band_of(v["diff"]), "prior_band": band_of(pr),
                     "reason": v["reason"]})
    if not recs:
        print("无结果"); return

    xl = [t["llm"] for t in recs]
    xp = [t["prior"] for t in recs]
    print("总体 Pearson r(LLM, 先验) = %.3f   （越接近 0 说明两者越独立）\n" % pearson(xl, xp))

    def dist(key):
        d = {}
        for t in recs:
            d[t[key]] = d.get(t[key], 0) + 1
        return d
    print("档位分布  LLM:", dist("llm_band"))
    print("档位分布  先验:", dist("prior_band"))

    print("\n--- 分歧最大的 10 题（|LLM − 先验| 降序）---")
    for t in sorted(recs, key=lambda z: -abs(z["llm"] - z["prior"]))[:10]:
        print("  %-14s %-4s %s年 %s分  LLM %.2f(%s)  先验 %.2f(%s)  Δ%+.2f"
              % (t["qid"], t["type"], t["year"], t["score"], t["llm"],
                 t["llm_band"], t["prior"], t["prior_band"], t["llm"] - t["prior"]))
        print("      %s" % t["reason"])

    print("\n--- 各题型内部均值 ---")
    for ty in ("选择题", "填空题", "解答题"):
        sub = [t for t in recs if t["type"] == ty]
        if sub:
            print("  %s (n=%2d)  LLM 均值 %.2f   先验均值 %.2f   先验区间 [%.2f, %.2f]"
                  % (ty, len(sub), sum(t["llm"] for t in sub) / len(sub),
                     sum(t["prior"] for t in sub) / len(sub),
                     min(t["prior"] for t in sub), max(t["prior"] for t in sub)))

    out = os.path.join(os.path.dirname(BANK_DB), "..", "docs", "_llm_vs_prior.json")
    import json
    with open(out, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    print("\n明细已写 %s" % os.path.normpath(out))


if __name__ == "__main__":
    main()
