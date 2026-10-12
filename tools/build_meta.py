# -*- coding: utf-8 -*-
"""从站点拉取完整知识点树 + 试卷元数据，存入本地题库的 meta 表。
同时生成 knowledge point -> 题量 的映射。
"""
import json
import os
import sqlite3
import ssl
import time
import urllib.request
from paths import TOOLS, ZTQ_DB

BASE = "https://zhentiqiang.com"
DB = ZTQ_DB
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"

c = sqlite3.connect(DB)
c.execute("""CREATE TABLE IF NOT EXISTS meta(
    k TEXT PRIMARY KEY, v TEXT)""")
c.execute("""CREATE TABLE IF NOT EXISTS kp(
    kp_id INTEGER, kp_name TEXT, parent_id INTEGER, level INTEGER, sort INTEGER)""")
c.execute("""CREATE TABLE IF NOT EXISTS paper(
    paper_id INTEGER, group_id INTEGER, year TEXT, name TEXT, question_cnt INTEGER)""")
c.commit()


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA,
                                                       "Referer": BASE + "/kaoyan/math"})
    with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


GROUPS = {8: "数学一", 9: "数学二", 10: "数学三"}

# ---------- 1. 知识点树 ----------
c.execute("DELETE FROM kp")
all_kp = {}
for gid, gname in GROUPS.items():
    d = get(f"/api/knowledge_tree/{gid}")
    # 该接口直接返回数组；兼容包装成对象的情况
    if isinstance(d, list):
        tree = d
    else:
        tree = d.get("knowledge_tree") or d.get("tree") or []
    if not tree:
        print(f"  group {gid} knowledge_tree 为空, keys={list(d.keys())}")
        continue

    def walk(nodes, parent=None, level=1):
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                continue
            nid, nm = n.get("id"), (n.get("name") or "").strip()
            if nid is not None:
                all_kp[int(nid)] = (nm, parent, level, i)
            kids = n.get("children") or n.get("sub") or []
            if kids:
                walk(kids, int(nid) if nid is not None else parent, level + 1)

    walk(tree)
    print(f"  group {gid} ({gname}): 知识点 {len(all_kp)} 个累计")

for nid, (nm, par, lv, so) in sorted(all_kp.items()):
    c.execute("INSERT INTO kp VALUES(?,?,?,?,?)", (nid, nm, par, lv, so))
c.commit()
print(f"知识点树入库: {len(all_kp)} 个")

# ---------- 2. 试卷元数据 ----------
c.execute("DELETE FROM paper")
np = 0
for gid, gname in GROUPS.items():
    d = get(f"/api/papers/{gid}")
    plist = d if isinstance(d, list) else (d.get("papers") or [])
    for p in plist:
        qs = p.get("questions") or []
        c.execute("INSERT INTO paper VALUES(?,?,?,?,?)",
                  (p.get("id"), gid, p.get("year") or p.get("name"),
                   p.get("name") or f"{gid}卷", len(qs)))
        np += 1
c.commit()
print(f"试卷元数据入库: {np} 份")

c.execute("INSERT OR REPLACE INTO meta VALUES('built_at',?)", (str(time.time()),))
c.execute("INSERT OR REPLACE INTO meta VALUES('groups',?)", (json.dumps(GROUPS, ensure_ascii=False),))
c.commit()

# ---------- 3. 知识点题量统计 ----------
# 知识点名不在 question 表列中，存在 raw JSON 里，用 json_extract 取
print("\n知识点 -> 题量 (前 15):")
try:
    rows = c.execute("""
        SELECT json_extract(raw,'$.knowledge_point_name') kp,
               COUNT(*) n,
               SUM(CASE WHEN answer<>'' THEN 1 ELSE 0 END) a
        FROM question WHERE raw IS NOT NULL AND raw<>''
        GROUP BY kp ORDER BY n DESC LIMIT 15""").fetchall()
    for r in rows:
        print(f"   {str(r[0])[:34]:36} 题量={r[1]:4} 有答案={r[2]:4}")
except Exception as e:
    print("  json_extract 不可用:", e)

print("\n按 group / 年份 分布:")
for r in c.execute("""SELECT group_id, COUNT(*) FROM question
                      GROUP BY group_id ORDER BY CAST(group_id AS INT)"""):
    print(f"   group {r[0]}: {r[1]} 题")

print("\n试卷年表 (group 8):")
for r in c.execute("SELECT year,paper_id,question_cnt FROM paper WHERE group_id=8 ORDER BY CAST(year AS INT) DESC"):
    print(f"   {r[0]}年 paper_id={r[1]:3} 题数={r[2]}")
