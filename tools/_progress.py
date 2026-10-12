# -*- coding: utf-8 -*-
"""_progress.py —— 打印两个长任务的进度快照。"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB                 # noqa: E402

DB = BANK_DB


def snap():
    c = sqlite3.connect(DB)
    llm = c.execute("SELECT COUNT(*) FROM question WHERE llm_diff IS NOT NULL").fetchone()[0]
    site = c.execute("SELECT COUNT(*) FROM question WHERE group_id IN ('8','9','10')"
                     " AND TRIM(COALESCE(stem,''))<>''").fetchone()[0]
    allstem = c.execute("SELECT COUNT(*) FROM question"
                        " WHERE TRIM(COALESCE(stem,''))<>''").fetchone()[0]
    c.close()
    return llm, site, allstem


if __name__ == "__main__":
    a = snap()
    print("T0  llm=%d/599  site_stem=%d/1224  any_stem=%d" % a)
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    time.sleep(secs)
    b = snap()
    print("T1  llm=%d/599  site_stem=%d/1224  any_stem=%d" % b)
    print("增量 (%ds): llm +%d   site_stem +%d" % (secs, b[0] - a[0], b[1] - a[1]))
