# -*- coding: utf-8 -*-
"""端到端验证：客观判分 + SRS 调度 + 统计。用伪造输入驱动 quiz 的核心逻辑。"""
import sys
import time
import sqlite3
sys.path.insert(0, TOOLS)
from ztq import DB, srs_update, log
from paths import TOOLS

c = sqlite3.connect(DB)

print("=" * 74)
print("[1] 抽题：优先有答案的选择题")
rows = c.execute("""
    SELECT q.id, q.paper_name, q.number, q.answer, COALESCE(s.wrong_cnt,0), COALESCE(s.seen,0)
    FROM question q LEFT JOIN srs s ON s.qid=q.id
    WHERE q.answer<>''
    ORDER BY RANDOM() LIMIT 8""").fetchall()
for r in rows:
    print(f"   qid={r[0]} {r[1]}年 第{r[2]}题  答案={r[3]}  错{r[4]}/做{r[5]}")

print("\n[2] 模拟作答：单数题答对，双数题答错，验证判分与调度")
T0 = time.time()
for idx, (qid, paper, num, ans, w, s) in enumerate(rows):
    key = ans.strip().upper()
    picked = key if idx % 2 == 0 else ("A" if key != "A" else "B")
    ok = 1 if picked == key else 0
    srs_update(c, qid, ok)
    log(c, qid, "answer", picked, ok, note="e2e-test")
    st = c.execute("SELECT seen,right_cnt,wrong_cnt,streak,ease,due FROM srs WHERE qid=?", (qid,)).fetchone()
    due_in = (st[5] - T0) / 86400.0
    print(f"   qid={qid} 选{picked} 正确{key} -> {'✅对' if ok else '❌错'}  "
          f"seen={st[0]} 对={st[1]} 错={st[2]} streak={st[3]} ease={st[4]:.2f} 下次+{due_in:.1f}天")

print("\n[3] 验证答对→间隔拉长、答错→立即重来")
for qid, *_ in rows[:3]:
    r = c.execute("SELECT qid,seen,streak,due-? FROM srs WHERE qid=?", (T0, qid)).fetchone()
    print(f"   qid={r[0]} seen={r[1]} streak={r[2]} due_in={r[3]/86400:.2f}天")

print("\n[4] 到期队列是否真的能筛出答错的题（due<=now => due_in<=0）")
time.sleep(1)
due = c.execute("SELECT COUNT(*) FROM srs WHERE due<=?", (time.time(),)).fetchone()[0]
print(f"   当前到期(立即复习)题数 = {due}  (应为答错的 4 题)")

print("\n[5] 清理测试记录，恢复干净状态")
c.execute("DELETE FROM srs WHERE qid IN (%s)" % ",".join(str(r[0]) for r in rows))
c.execute("DELETE FROM record WHERE note='e2e-test'")
c.commit()
print("   已清理; 剩余 srs 行数 =", c.execute("SELECT COUNT(*) FROM srs").fetchone()[0],
      "record 行数 =", c.execute("SELECT COUNT(*) FROM record").fetchone()[0])
