# -*- coding: utf-8 -*-
"""Final statistics snapshot."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as P  # noqa: E402

c = sqlite3.connect(P.BANK_DB)
c.row_factory = sqlite3.Row
one = lambda s: c.execute(s).fetchone()[0]

print("题目      :", one("SELECT COUNT(*) FROM question"))
print("有答案    :", one("SELECT COUNT(*) FROM question WHERE TRIM(COALESCE(answer,''))<>''"))
print("可判分    :", one("SELECT COUNT(*) FROM question WHERE gradeable=1"))
print("  src 分布:", dict(c.execute(
    "SELECT answer_src,COUNT(*) FROM question "
    "WHERE TRIM(COALESCE(answer,''))<>'' GROUP BY answer_src")))
print("  flag   :", dict(c.execute(
    "SELECT answer_flag,COUNT(*) FROM question GROUP BY answer_flag")))
print("记录/SRS  :", one("SELECT COUNT(*) FROM record"), "/", one("SELECT COUNT(*) FROM srs"))
print("LLM 难度  :", dict(c.execute(
    "SELECT llm_level,COUNT(*) FROM question WHERE llm_diff IS NOT NULL GROUP BY llm_level")))

PROJ = P.PROJ
nf = 0
sz = 0
for r, _, fs in os.walk(PROJ):
    if "__pycache__" in r:
        continue
    for x in fs:
        nf += 1
        sz += os.path.getsize(os.path.join(r, x))
print("文件      : %d 个 / %.1f MB" % (nf, sz / 1048576))
