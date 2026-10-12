# -*- coding: utf-8 -*-
"""收尾：清理匿名测试评论并输出库最终状态。"""
import sys
import json
import sqlite3
sys.path.insert(0, TOOLS)
from ztq import req, DB
from paths import TOOLS

st, d, _ = req("/api/comments/41683", method="DELETE")
print("DELETE /api/comments/41683 ->", st, str(d)[:200])

st, d, _ = req("/api/comments/1942")
print("1942 剩余评论数:", (d or {}).get("total_count"))
print("评论内容:", [c.get("content") for c in (d or {}).get("comments", [])][:6])

print()
c = sqlite3.connect(DB)
q = ("SELECT COUNT(*), SUM(answer<>''), "
     "SUM(CASE WHEN json_extract(raw,'$.is_multiple_choice')=1 THEN 1 ELSE 0 END) FROM question")
tot, ans, mc = c.execute(q).fetchone()
print(f"题目 {tot} | 有答案 {ans} | 选择题 {mc}")

for r in c.execute("SELECT group_id, COUNT(*), SUM(answer<>'') FROM question GROUP BY group_id ORDER BY group_id"):
    print(f"  group {r[0]}: {r[1]} 题, {r[2]} 有答案")

print("\n按年份有答案数（group 合计数）:")
for r in c.execute("""SELECT year, COUNT(*), SUM(answer<>'') FROM question
                      GROUP BY year ORDER BY CAST(year AS INT) DESC LIMIT 5"""):
    print(f"  {r[0]}: {r[2]}/{r[1]}")
