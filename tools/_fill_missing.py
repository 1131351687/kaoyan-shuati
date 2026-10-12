# -*- coding: utf-8 -*-
"""补齐 llm_solution_filter 漏判的条目（sol_ok 仍为 NULL 的候选）。

做法：单条成批（batch=1）重跑，规避大批量时 agnes 的截断问题。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3
import llm_solution_filter as F
from paths import BANK_DB


def main():
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row
    rows = F.load_candidates(limit=None)
    pend = []
    for r in rows:
        got = c.execute("SELECT sol_ok FROM question WHERE id=?", (r["qid"],)).fetchone()
        if got is not None and got["sol_ok"] is None:
            pend.append(r)
    print(f"待补 {len(pend)} 条")
    if not pend:
        return 0

    n = 0
    for i, r in enumerate(pend, 1):
        try:
            txt = F.call_llm(F.build_prompt([r]))
            v = F.parse_verdicts(txt, 1)
        except Exception as e:                       # noqa: BLE001
            print(f"  [{i}] {r['year']}#{r['num']} 失败: {str(e)[:90]}")
            continue
        if 0 not in v:
            print(f"  [{i}] {r['year']}#{r['num']} 仍解析失败")
            continue
        ok, conf, why = v[0]
        c.execute("UPDATE question SET sol_ok=?,sol_conf=?,sol_why=? WHERE id=?",
                  (1 if ok else 0, conf, why, r["qid"]))
        c.commit()
        n += 1
        print(f"  [{i}] {r['year']}#{r['num']} [{'✓' if ok else '✗'}] {why[:56]}")
    print(f"\n补写 {n}/{len(pend)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
