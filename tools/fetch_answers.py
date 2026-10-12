# -*- coding: utf-8 -*-
"""
fetch_answers.py —— 全量回填答案
破译要点: GET /api/question_info/<qid> 匿名返回 {"answer":"A","is_multiple_choice":true,...}
          之前误判为 404 是因为测试用的 id=1 不存在（"题目不存在"）。
"""
import os
import re
import sys
import json
import time
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ztq import req, DB

WORKERS_DELAY = 0.12


def main(only_missing=True, limit=None):
    c = sqlite3.connect(DB)
    sql = "SELECT id FROM question"
    if only_missing:
        sql += " WHERE answer IS NULL OR answer=''"
    sql += " ORDER BY CAST(year AS INT) DESC, CAST(number AS INT)"
    ids = [r[0] for r in c.execute(sql)]
    if limit:
        ids = ids[:limit]
    print(f"[*] 待回填 {len(ids)} 题")

    ok = fail = noans = 0
    t0 = time.time()
    for i, qid in enumerate(ids, 1):
        st, d, _ = req(f"/api/question_info/{qid}")
        if st == 200 and isinstance(d, dict) and d.get("success"):
            ans = (d.get("answer") or "").strip()
            mc = 1 if d.get("is_multiple_choice") else 0
            c.execute("""UPDATE question SET answer=?, qtype=?, raw=json_patch(COALESCE(raw,'{}'), ?)
                         WHERE id=?""",
                      (ans, d.get("question_type") or 1,
                       json.dumps({"is_multiple_choice": bool(mc), "paper_id": d.get("paper_id"),
                                   "index": d.get("index"), "knowledge_point_name": d.get("knowledge_point_name")},
                                  ensure_ascii=False), qid))
            if ans:
                ok += 1
            else:
                noans += 1
        else:
            fail += 1
        if i % 100 == 0 or i == len(ids):
            c.commit()
            el = time.time() - t0
            print(f"  {i}/{len(ids)}  有答案={ok} 空={noans} 失败={fail}  "
                  f"{el:.0f}s  {i/el:.1f} req/s")
        time.sleep(WORKERS_DELAY)
    c.commit()

    tot = c.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    got = c.execute("SELECT COUNT(*) FROM question WHERE answer<>''").fetchone()[0]
    print(f"\n[✓] {got}/{tot} 题已带答案   用时 {time.time()-t0:.0f}s")
    print("\n[答案分布]")
    for r in c.execute("""SELECT answer, COUNT(*) FROM question WHERE answer<>''
                          GROUP BY answer ORDER BY 2 DESC"""):
        print(f"   {r[0]:>4} : {r[1]}")
    print("\n[按年份覆盖率]")
    for r in c.execute("""SELECT year, COUNT(*), SUM(answer<>'') FROM question
                          GROUP BY year ORDER BY CAST(year AS INT) DESC"""):
        print(f"   {r[0]}  {r[2]}/{r[1]}")
    return got, tot


if __name__ == "__main__":
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    main(only_missing=(sys.argv[1] != "all" if len(sys.argv) > 1 else True), limit=lim)
