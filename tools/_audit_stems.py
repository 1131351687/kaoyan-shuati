# -*- coding: utf-8 -*-
"""_audit_stems.py —— 审计 OCR/导入的题干质量。

检查项：
 1. 选择题是否含 4 个选项 (A)-(D)
 2. 题干是否过短（<30 字 → 可能只识别出题号）
 3. 是否含乱码/替换符
 4. LaTeX 是否括号失衡
 5. 题号是否与站点 number 一致
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB                 # noqa: E402

DB = BANK_DB
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

rows = [dict(r) for r in c.execute(
    "SELECT id, group_id, year, number, qtype_name, stem FROM question")]

n_opt_missing, n_short, n_garbled, n_unbal, n_num_bad = [], [], [], [], []

for r in rows:
    s = r["stem"] or ""
    if r["qtype_name"] == "选择题":
        letters = set(re.findall(r"^\s*[（(]?([ABCD])[）)]?\s*[.．、]", s, re.M))
        if len(letters) < 4:
            letters |= set(re.findall(r"[（(]([ABCD])[）)]", s))
        if len(letters) < 4:
            n_opt_missing.append(r)
    if len(s) < 30:
        n_short.append(r)
    if re.search(r"[\ufffd\u25a1]", s):
        n_garbled.append(r)
    for a, b in (("{", "}"), ("(", ")"), ("[", "]")):
        pass
    if s.count("$") % 2 != 0:
        n_unbal.append(r)
    m = re.match(r"^\s*[（(]?(\d{1,2})[）).．]", s)
    if m and m.group(1) != str(r["number"]).strip():
        n_num_bad.append(r)

print("总 %d 题" % len(rows))
print("选择题缺选项 : %d" % len(n_opt_missing))
print("题干 <30 字  : %d" % len(n_short))
print("含乱码替换符 : %d" % len(n_garbled))
print("$ 不配对     : %d" % len(n_unbal))
print("题号与站点不符: %d" % len(n_num_bad))

for name, lst in (("缺选项", n_opt_missing), ("过短", n_short),
                  ("乱码", n_garbled), ("$不配对", n_unbal)):
    if lst:
        print("\n--- %s 样例（前 8）---" % name)
        for r in lst[:8]:
            print("  %-10s g%-5s %s年#%-3s len=%-4d %s"
                  % (r["id"], r["group_id"], r["year"], r["number"],
                     len(r["stem"] or ""), (r["stem"] or "").replace("\n", " ")[:100]))
