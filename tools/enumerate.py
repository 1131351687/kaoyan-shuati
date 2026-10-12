# -*- coding: utf-8 -*-
"""枚举全部试卷组 / 论文 / 题目覆盖面，确认抓取完整性。"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ztq import req, BASE

print("=" * 78)
print("[A] 枚举 group_id 1..40，看哪些有效")
valid = []
for g in range(1, 41):
    st, d, _ = req(f"/api/bootstrap?group_id={g}")
    if isinstance(d, dict) and d.get("success"):
        papers = d.get("papers") or []
        groups = d.get("papergroups") or []
        kt = d.get("knowledge_tree") or []
        nq = sum(len(p.get("questions") or []) for p in papers)
        valid.append((g, len(papers), nq, [x.get("name") for x in groups]))
        print(f"  group {g:>3}: {len(papers):>3} 试卷, {nq:>5} 题, 组名={[x.get('name') for x in groups]}")
print(f"\n有效 group: {[v[0] for v in valid]}")

print("\n" + "=" * 78)
print("[B] group 8 的试卷组明细")
st, d, _ = req("/api/bootstrap?group_id=8")
for g in d.get("papergroups") or []:
    print("  ", json.dumps(g, ensure_ascii=False))

print("\n[C] group 8 试卷列表（id/名称/题数）")
for p in d.get("papers") or []:
    print(f"   paper_id={p.get('id'):>4}  name={p.get('name'):<10} "
          f"题数={len(p.get('questions') or []):>3}  keys={sorted(p.keys())}")

print("\n[D] group 8 知识点树")
def walk(nodes, lv=0):
    for n in nodes:
        print("   " + "  " * lv + f"- [{n.get('id')}] {n.get('name')}")
        walk(n.get("children") or [], lv + 1)
walk(d.get("knowledge_tree") or [])

print("\n[E] 单题对象完整字段（首个）")
q = (d["papers"][0]["questions"] or [None])[0]
print(json.dumps(q, ensure_ascii=False, indent=1))
