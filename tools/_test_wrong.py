# -*- coding: utf-8 -*-
"""错题本 + 答题计时的临时验证脚本（跑完清理，不留数据）。"""
import os
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BANK_PY = os.path.join(HERE, "..", "app", "bank.py")
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import bank  # noqa: E402

c = bank.conn()

# 取 3 道可判分的站点题造数据
rows = c.execute("SELECT id, answer FROM question WHERE gradeable=1 AND group_id='8' "
                 "ORDER BY CAST(year AS INT) DESC LIMIT 3").fetchall()
assert len(rows) == 3, rows
qids = [r["id"] for r in rows]
print("测试题:", qids)

# 清掉可能的残留
c.execute("DELETE FROM record WHERE qid IN (%s)" % ",".join("?" * len(qids)), qids)
c.execute("DELETE FROM srs    WHERE qid IN (%s)" % ",".join("?" * len(qids)), qids)
c.commit()

now = time.time()
# q1: 错两次后对一次（已攻克）；q2: 只错一次（未攻克）；q3: 只对（不进错题本）
bank.log(c, qids[0], "answer", "A", 0, elapsed=12.5)
bank.log(c, qids[0], "answer", "B", 0, elapsed=31.0)
bank.log(c, qids[0], "answer", rows[0]["answer"], 1, elapsed=8.2)
bank.log(c, qids[1], "answer", "D", 0, elapsed=95.4)
bank.log(c, qids[2], "answer", rows[2]["answer"], 1, elapsed=4.1)

# 让 q1 处于「已攻克」（streak>0），q2 未攻克
for qid, ok in ((qids[0], 1), (qids[1], 0), (qids[2], 1)):
    bank.srs_update(c, qid, ok)

print("\n=== record 表内容 ===")
for r in c.execute("SELECT qid,action,value,ok,elapsed,last_wrong FROM record "
                   "WHERE qid IN (%s) ORDER BY id" % ",".join("?" * len(qids)), qids):
    lw = time.strftime("%H:%M:%S", time.localtime(r["last_wrong"])) if r["last_wrong"] else "—"
    print(f"  {r['qid']:<6} {r['action']:<7} {r['value']:<3} ok={r['ok']} "
          f"elapsed={r['elapsed']} last_wrong={lw}")

def run_wrong(*extra):
    p = subprocess.run([sys.executable, BANK_PY, "wrong", *extra],
                       capture_output=True, text=True, encoding="utf-8")
    print(p.stdout, end="")
    if p.returncode:
        print("  [stderr]", p.stderr[:400])
    return p.stdout


print("\n=== 错题本全量 ===")
run_wrong("--top", "5")

print("\n=== 错题本 --unsolved-only（应只剩 q2）===")
run_wrong("--unsolved-only", "--top", "5")

print("\n=== 清理 ===")
c.execute("DELETE FROM record WHERE qid IN (%s)" % ",".join("?" * len(qids)), qids)
c.execute("DELETE FROM srs    WHERE qid IN (%s)" % ",".join("?" * len(qids)), qids)
c.commit()
print("  record:", c.execute("SELECT COUNT(*) FROM record").fetchone()[0],
      " srs:", c.execute("SELECT COUNT(*) FROM srs").fetchone()[0])
