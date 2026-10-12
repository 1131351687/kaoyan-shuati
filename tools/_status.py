# -*- coding: utf-8 -*-
"""_status.py —— 数据现状总览（stem/llm/answer 覆盖情况）。"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB                 # noqa: E402

DB = BANK_DB
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def one(sql, a=()):
    return c.execute(sql, a).fetchone()[0]


print("=== 总量 ===")
print("  题目总数      :", one("SELECT COUNT(*) FROM question"))
print("  站点真题 8/9/10:", one("SELECT COUNT(*) FROM question WHERE group_id IN ('8','9','10')"))
print("  本地老卷 old* :", one("SELECT COUNT(*) FROM question WHERE group_id LIKE 'old%'"))
print("  模拟卷 mock*  :", one("SELECT COUNT(*) FROM question WHERE group_id LIKE 'mock%'"))

print("\n=== 文字题干（stem）===")
tot = one("SELECT COUNT(*) FROM question")
st = one("SELECT COUNT(*) FROM question WHERE TRIM(COALESCE(stem,''))<>''")
print("  有题干        : %d / %d  (%.1f%%)" % (st, tot, 100.0 * st / tot))
print("  站点题有题干  : %d / 1224" % one(
    "SELECT COUNT(*) FROM question WHERE group_id IN ('8','9','10')"
    " AND TRIM(COALESCE(stem,''))<>''"))
print("  站点题无题干  : %d" % one(
    "SELECT COUNT(*) FROM question WHERE group_id IN ('8','9','10')"
    " AND TRIM(COALESCE(stem,''))=''"))

print("\n=== LLM 难度 ===")
print("  已评          : %d / %d" % (
    one("SELECT COUNT(*) FROM question WHERE llm_diff IS NOT NULL"), tot))
print("  分布          :", dict(c.execute(
    "SELECT llm_level, COUNT(*) FROM question WHERE llm_level IS NOT NULL"
    " GROUP BY llm_level").fetchall()))

print("\n=== 按分组 x 题型 的 stem 覆盖 ===")
for r in c.execute("""
    SELECT CASE WHEN group_id LIKE 'mock%' THEN '模拟卷'
                WHEN group_id LIKE 'old%'  THEN '本地老卷'
                ELSE '站点真题(' || group_id || ')' END g,
           qtype_name, COUNT(*) n,
           SUM(CASE WHEN TRIM(COALESCE(stem,''))<>'' THEN 1 ELSE 0 END) s
    FROM question GROUP BY g, qtype_name ORDER BY g, qtype_name"""):
    print("  %-14s %-6s %4d 题, 有题干 %4d (%.0f%%)"
          % (r["g"], r["qtype_name"], r["n"], r["s"], 100.0 * r["s"] / r["n"]))
