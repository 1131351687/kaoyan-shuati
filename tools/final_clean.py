# -*- coding: utf-8 -*-
"""最终清理与状态确认。"""
import sqlite3
from paths import TOOLS, ZTQ_DB
c = sqlite3.connect(ZTQ_DB)
c.execute("DELETE FROM record WHERE note IN ('e2e-test','local-only') OR action='dump'")
c.commit()
for t in ("question", "record", "srs"):
    print(f"  {t:10}: {c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]}")
print("  有答案题数 :", c.execute("SELECT COUNT(*) FROM question WHERE answer<>''").fetchone()[0])
