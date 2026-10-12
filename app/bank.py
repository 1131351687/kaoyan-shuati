# -*- coding: utf-8 -*-
"""本地题库刷题客户端 —— 纯离线，不连站点。

命令：
  python bank.py list   [--group 8] [--year 2025] [--kp 微分] [--hard] [--limit 30]
  python bank.py show   <qid>                查看题目详情
  python bank.py quiz   [-n 20] [--group 8] [--kp X] [--due] [--hard] [--new]
  python bank.py review                      到期复习队列（SRS）
  python bank.py stats  [--group 8]          统计报告
  python bank.py weak   [--top 20]           薄弱知识点排行
  python bank.py hard   [--top 20] [--min-n 50]  全站最难题排行
  python bank.py export --anki <out.apkg 或 out.csv>
  python bank.py serve  [--port 8899]        启动本地浏览网页

判分题（有 A/B/C/D 答案）自动评分并更新 SM-2；填空/解答题走自评。
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

# 目录：app/bank.py -> app -> 考研刷题
APP = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(APP)
DATA = os.path.join(PROJ, "data")

DB = os.path.join(DATA, "ztq_bank.db")
IMGROOT = os.path.join(DATA, "images")
INDEX_JSON = os.path.join(APP, "index.json")
VENDOR = os.path.join(APP, "vendor")

# Windows 控制台 utf-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 工具
def migrate(c):
    """幂等补列：老数据库缺 elapsed / last_wrong（答题计时、错题本）时补上。"""
    cols = {r[1] for r in c.execute("PRAGMA table_info(record)")}
    if "elapsed" not in cols:
        c.execute("ALTER TABLE record ADD COLUMN elapsed REAL")
    if "last_wrong" not in cols:
        c.execute("ALTER TABLE record ADD COLUMN last_wrong REAL")
        # 回填历史错题时间：用该题最近一次 ok=0 的记录时间
        c.execute("""UPDATE record SET last_wrong =
                       (SELECT MAX(r2.ts) FROM record r2
                         WHERE r2.qid=record.qid AND r2.ok=0)
                     WHERE ok=0""")
    c.commit()


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    migrate(c)
    return c


def hr(ch="─", n=76):
    print(ch * n)


def fmt_dur(sec):
    """秒 → 人类可读（'3.2 秒' / '1 分 05 秒' / '—'）。"""
    if sec is None:
        return "—"
    sec = float(sec)
    if sec < 60:
        return f"{sec:.1f} 秒"
    m, s = divmod(int(round(sec)), 60)
    return f"{m} 分 {s:02d} 秒"


def img_path(row):
    rel = row["img_local"] or ""
    return os.path.join(IMGROOT, rel) if rel else ""


def show_question(row, reveal=False, idx=None):
    hr("━")
    head = f"[{row['group_name']}] {row['year']}年 第{row['number']}题"
    if idx is not None:
        head = f"({idx}) " + head
    print(head + f"   {row['qtype_name']}  {row['score']}分   知识点: {row['kp_name'] or '—'}")

    # 模拟卷：题干是文字（本地 md）；真题：题干是题图
    stem = row["stem"] if "stem" in row.keys() else ""
    if stem:
        print("  ── 题干 ──")
        for ln in stem.splitlines():
            print("   " + ln.strip())
        print("  ──────────")
    else:
        print(f"  本地题图: {img_path(row)}")

    if row["diff_n"]:
        print(f"  全站画像: 熟练{row['happy']} 不熟{row['maybe']} 不会{row['sad']} "
              f"→ 熟练率 {row['mastery']*100:.1f}%  (样本 {row['diff_n']})")
    if reveal:
        if row["gradeable"]:
            print(f"  ▶ 标准答案: {row['answer']}")
        elif row["answer"]:
            src = "本地解析" if (row["answer_src"] or "") == "local" else "站点"
            note = row["answer_note"] or ""
            print(f"  ▶ 答案（{src}{'·' + note if note else ''}）: {row['answer']}")
        elif (row["answer_src"] or "") == "mock":
            print(f"  ▶ 本地模拟卷：答案在扫描版解析 PDF 中（无文字层），未数字化")
        elif row["qtype"] == 2:
            print(f"  ▶ 本站未收录答案（填空题，站点自身 answer 为空）")
        else:
            print(f"  ▶ 本站未收录答案（解答题，站点自身 answer 为空）")


def parse_multi(ans):
    return set(ch for ch in (ans or "").upper() if ch in "ABCD")


# ---------------------------------------------------------------- SM-2
def srs_update(c, qid, ok):
    """SM-2 变体：答对拉长间隔，答错立即重来。"""
    now = time.time()
    r = c.execute("SELECT * FROM srs WHERE qid=?", (qid,)).fetchone()
    if r is None:
        c.execute("INSERT INTO srs(qid,seen,right_cnt,wrong_cnt,streak,ease,last_ts,due)"
                  " VALUES(?,0,0,0,0,2.5,?,?)", (qid, now, now))
        r = c.execute("SELECT * FROM srs WHERE qid=?", (qid,)).fetchone()

    seen, right, wrong, streak, ease, due = (r["seen"], r["right_cnt"], r["wrong_cnt"],
                                             r["streak"], r["ease"], r["due"])
    seen += 1
    if ok:
        right += 1
        streak += 1
        ease = min(2.8, ease + 0.1)
        if streak == 1:
            iv = 1.0
        elif streak == 2:
            iv = 3.0
        else:
            iv = min(180.0, 3.0 * (ease ** (streak - 2)))
    else:
        wrong += 1
        streak = 0
        ease = max(1.3, ease - 0.3)
        iv = 0.0
    due = now + iv * 86400
    c.execute("""UPDATE srs SET seen=?,right_cnt=?,wrong_cnt=?,streak=?,ease=?,
                 last_ts=?,due=? WHERE qid=?""",
              (seen, right, wrong, streak, ease, now, due, qid))
    c.commit()
    return iv, ease, streak


def log(c, qid, action, value, ok, note="", elapsed=None):
    """写一条作答记录。

    elapsed: 作答耗时（秒），None 表示未计时。
    答错时同时写 last_wrong，供「错题本」按时间倒序聚合。
    """
    now = time.time()
    c.execute("INSERT INTO record(qid,ts,action,value,ok,note,elapsed,last_wrong)"
              " VALUES(?,?,?,?,?,?,?,?)",
              (qid, now, action, str(value), ok, note, elapsed,
               (now if not ok else None)))
    c.commit()


# ---------------------------------------------------------------- 选池
def pool(c, group=None, year=None, kp=None, due_only=False, hard=False,
         new_only=False, limit=None, gradeable_only=False):
    sql = ["SELECT q.* FROM question q"]
    w, a = [], []
    if due_only or new_only:
        sql.append("LEFT JOIN srs s ON s.qid=q.id")
    else:
        sql.append("LEFT JOIN srs s ON s.qid=q.id")
    if group:
        w.append("q.group_id=?"); a.append(str(group))
    if year:
        w.append("q.year=?"); a.append(str(year))
    if kp:
        w.append("(q.kp_name LIKE ? OR q.kp_lv1 LIKE ? OR q.kp_lv2 LIKE ?)")
        a += [f"%{kp}%"] * 3
    if gradeable_only:
        w.append("q.gradeable=1")
    if due_only:
        w.append("s.due IS NOT NULL AND s.due<=?")
        a.append(time.time())
    if new_only:
        w.append("(s.seen IS NULL OR s.seen=0)")
    if hard:
        w.append("q.diff_n>=20 AND q.mastery IS NOT NULL")
    if w:
        sql.append("WHERE " + " AND ".join(w))
    if hard:
        sql.append("ORDER BY q.mastery ASC")
    elif due_only:
        sql.append("ORDER BY s.due ASC")
    else:
        sql.append("ORDER BY RANDOM()")
    if limit:
        sql.append(f"LIMIT {int(limit)}")
    return c.execute(" ".join(sql), a).fetchall()


# ---------------------------------------------------------------- 命令
def cmd_list(args):
    c = conn()
    rows = pool(c, args.group, args.year, args.kp, hard=args.hard,
                limit=args.limit, gradeable_only=args.gradeable)
    print(f"\n共 {len(rows)} 题\n")
    print(f"{'qid':>6} {'卷':>6} {'题':>4} {'类型':<10} {'答案':<5} {'题量':>5}  {'熟练率':>7}  知识点")
    hr()
    for r in rows:
        ans = r["answer"] if r["gradeable"] else "—"
        m = f"{r['mastery']*100:5.1f}%" if r["mastery"] is not None else "   —  "
        print(f"{r['id']:>6} {r['year']:>6} {r['number']:>4} {r['qtype_name']:<10} "
              f"{ans:<5} {r['diff_n']:>5}  {m:>7}  {str(r['kp_name'])[:28]}")


def cmd_show(args):
    c = conn()
    r = c.execute("SELECT * FROM question WHERE id=?", (str(args.qid),)).fetchone()
    if not r:
        print(f"未找到题目 qid={args.qid}")
        return
    show_question(r, reveal=True)
    s = c.execute("SELECT * FROM srs WHERE qid=?", (str(args.qid),)).fetchone()
    if s:
        due_in = (s["due"] - time.time()) / 86400
        print(f"\n  你的记录: 做过{s['seen']}次 对{s['right_cnt']} 错{s['wrong_cnt']} "
              f"连对{s['streak']} ease={s['ease']:.2f} 下次 {due_in:+.2f} 天")
    else:
        print("\n  你的记录: 未做过")
    hist = c.execute("SELECT ts,action,value,ok FROM record WHERE qid=? ORDER BY ts DESC LIMIT 10",
                     (str(args.qid),)).fetchall()
    if hist:
        print("\n  作答历史:")
        for h in hist:
            t = datetime.fromtimestamp(h["ts"]).strftime("%m-%d %H:%M")
            print(f"    {t}  {h['action']:<8} {h['value']:<6} {'✓' if h['ok'] else '✗'}")


def cmd_quiz(args):
    c = conn()
    due_only = args.due
    only_gradeable = not args.any_type

    # 模拟卷的答案未能数字化（扫描 PDF），若该组一道判分题都没有，
    # 就别把用户挡在门外 —— 自动退化为自评模式。
    if only_gradeable and args.group:
        has_g = c.execute("SELECT COUNT(*) FROM question WHERE group_id=? AND gradeable=1",
                          (str(args.group),)).fetchone()[0]
        if not has_g:
            only_gradeable = False
            print(f"提示: 组 {args.group} 无判分题（答案未数字化），已切换为自评模式。")

    rows = pool(c, args.group, args.year, args.kp, due_only=due_only,
                hard=args.hard, new_only=args.new, limit=args.n,
                gradeable_only=only_gradeable)

    if not rows and due_only:
        print("到期队列为空 —— 没有需要复习的题。")
        return
    if not rows:
        print("没有匹配的题目。")
        return

    print(f"\n开始刷题: {len(rows)} 题"
          f"{'（到期复习）' if due_only else ''}"
          f"{'（全站难题优先）' if args.hard else ''}")
    print("操作: 输入 A/B/C/D 作答  |  d=看答案  s=跳过  q=退出\n")

    right = wrong = skip = 0
    t0 = time.time()

    for i, r in enumerate(rows, 1):
        show_question(r, reveal=False, idx=i)
        q_t0 = time.time()          # 本题开始计时
        if r["gradeable"]:
            print("\n  请选择 [A/B/C/D]: ", end="", flush=True)
            v = input().strip()
            if v.lower() == "q":
                break
            if v.lower() == "s":
                skip += 1
                print("  → 跳过\n")
                continue
            if v.lower() == "d":
                print(f"  → 答案: {r['answer']}\n")
                skip += 1
                continue

            key = parse_multi(r["answer"])
            picked = parse_multi(v)
            ok = 1 if (picked and picked == key) else 0
            elapsed = time.time() - q_t0
            iv, ease, streak = srs_update(c, r["id"], ok)
            log(c, r["id"], "answer", v.upper(), ok, elapsed=elapsed)
            if ok:
                right += 1
                print(f"  ✅ 正确（答案 {r['answer']}）  连对{streak}  "
                      f"用时 {fmt_dur(elapsed)}  下次 {iv:.1f} 天后\n")
            else:
                wrong += 1
                print(f"  ❌ 错误（你选 {v.upper()}，正确 {r['answer']}）  "
                      f"用时 {fmt_dur(elapsed)}  ease→{ease:.2f} 立即重来\n")
        else:
            # 非选择题：可能是本地解析回填了答案，也可能站点/模拟卷都没有
            has_local = bool(r["answer"])
            tip = ("本题为%s，本地解析已收录答案（可输入 d 查看）"
                   % r["qtype_name"]) if has_local else \
                  ("本题为%s，无收录答案" % r["qtype_name"])
            print(f"\n  {tip}，请自评:")
            print("    [1] 熟练   [2] 不熟   [3] 不会   "
                  + ("[d] 看答案   " if has_local else "")
                  + "[s] 跳过   [q] 退出")
            print("  选择: ", end="", flush=True)
            v = input().strip()
            if v.lower() == "d" and has_local:
                print(f"  → 答案（{r['answer_note'] or '本地'}）: {r['answer']}\n")
                skip += 1
                continue
            if v.lower() == "q":
                break
            if v.lower() == "s":
                skip += 1
                print("  → 跳过\n")
                continue
            ok = 1 if v == "1" else 0
            elapsed = time.time() - q_t0
            iv, ease, streak = srs_update(c, r["id"], ok)
            log(c, r["id"], "self", {"1": "熟练", "2": "不熟", "3": "不会"}.get(v, v),
                ok, elapsed=elapsed)
            print(f"  → 已记录{'（会）' if ok else '（需复习）'}  用时 {fmt_dur(elapsed)}\n")

    el = time.time() - t0
    tot = right + wrong
    hr("═")
    print(f"本轮结束: 作答 {tot} 题 (对 {right} / 错 {wrong})  跳过 {skip}  用时 {el/60:.1f} 分钟")
    if tot:
        print(f"正确率: {right/tot*100:.1f}%")
    d = c.execute("SELECT COUNT(*) FROM srs WHERE due<=?", (time.time(),)).fetchone()[0]
    print(f"当前待复习: {d} 题   累计已做: "
          f"{c.execute('SELECT COUNT(*) FROM srs').fetchone()[0]} 题")


def cmd_review(args):
    args.due = True
    args.group = getattr(args, "group", None)
    args.year = None
    args.kp = None
    args.hard = False
    args.new = False
    args.any_type = False
    args.n = getattr(args, "n", 50)
    cmd_quiz(args)


def cmd_stats(args):
    c = conn()
    where, a = "", []
    if args.group:
        where = "WHERE group_id=?"; a = [str(args.group)]

    print("\n" + "=" * 76)
    print("本地题库统计" + (f"  (group {args.group})" if args.group else ""))
    print("=" * 76)
    tot = c.execute(f"SELECT COUNT(*) FROM question {where}", a).fetchone()[0]
    ga = c.execute(f"SELECT COUNT(*) FROM question {where} {'AND' if where else 'WHERE'} gradeable=1", a).fetchone()[0]
    pct = f"{ga/tot*100:.1f}%" if tot else "—"
    print(f"题目总数: {tot}   可判分(有答案): {ga} ({pct})   不可判分: {tot-ga}")
    if not tot:
        print("\n⚠ 题库为空。这是空白框架版，请先导入数据（见 README「导入数据」）。")
    print(f"知识点: {c.execute('SELECT COUNT(*) FROM kp').fetchone()[0]}   "
          f"试卷: {c.execute('SELECT COUNT(*) FROM paper').fetchone()[0]}")

    print("\n按 group:")
    for r in c.execute("""SELECT group_name, COUNT(*) n,
                                 SUM(gradeable) g FROM question
                          GROUP BY group_id
                          ORDER BY (group_id LIKE 'mock%'), CAST(group_id AS INT)"""):
        print(f"   {r['group_name']}: {r['n']} 题 ({r['g']} 有答案)")

    print("\n学习进度:")
    s = c.execute("""SELECT COUNT(*) n, SUM(seen) sv, SUM(right_cnt) rc,
                            SUM(wrong_cnt) wc FROM srs""").fetchone()
    print(f"   已学 {s['n'] or 0} 题   总作答 {s['sv'] or 0} 次   "
          f"对 {s['rc'] or 0} / 错 {s['wc'] or 0}")
    if s["n"]:
        seen = c.execute("SELECT COUNT(*) FROM srs WHERE seen>0").fetchone()[0]
        mast = c.execute("SELECT COUNT(*) FROM srs WHERE streak>=3").fetchone()[0]
        print(f"   覆盖率 {seen/tot*100:.1f}%   已掌握(连对≥3) {mast} 题 ({mast/tot*100:.1f}%)")
        d = c.execute("SELECT COUNT(*) FROM srs WHERE due<=?", (time.time(),)).fetchone()[0]
        print(f"   待复习 {d} 题")
    else:
        print("   (还没开始刷题)")

    print("\n按年份题量(有答案):")
    for r in c.execute(f"""SELECT year, COUNT(*) n, SUM(gradeable) g FROM question {where}
                           GROUP BY year ORDER BY CAST(year AS INT) DESC""", a):
        bar = "█" * int((r["g"] or 0) / 2)
        print(f"   {r['year']}: {r['n']:>3} 题  有答案 {r['g']:>2}  {bar}")


def cmd_weak(args):
    c = conn()
    print(f"\n薄弱知识点排行 —— 依据你自己的错题 (共 {args.top} 项)\n")
    print(f"{'知识点':<30} {'做过':>5} {'错':>5} {'正确率':>8} {'全站熟练率':>10}")
    hr()
    rows = c.execute("""
        SELECT q.kp_name,
               COUNT(DISTINCT s.qid)            n,
               SUM(s.wrong_cnt)                 w,
               SUM(s.right_cnt)                 r,
               AVG(q.mastery)                   m
        FROM srs s JOIN question q ON q.id=s.qid
        WHERE s.seen>0
        GROUP BY q.kp_name
        HAVING w>0
        ORDER BY (SUM(s.right_cnt)*1.0/NULLIF(SUM(s.right_cnt)+SUM(s.wrong_cnt),0)) ASC
        LIMIT ?""", (args.top,)).fetchall()

    if not rows:
        print("  暂无错题记录 —— 先跑 `python bank.py quiz` 做几题。")
    for r in rows:
        tot = (r["r"] or 0) + (r["w"] or 0)
        acc = (r["r"] or 0) / tot * 100 if tot else 0
        m = f"{r['m']*100:5.1f}%" if r["m"] is not None else "   —  "
        print(f"{str(r['kp_name'])[:29]:<30} {r['n']:>5} {r['w']:>5} "
              f"{acc:>7.1f}% {m:>10}")

    print("\n全站最难知识点(与你的进度无关，仅供参考):")
    for r in c.execute("""SELECT kp_name, COUNT(*) n, AVG(mastery) m, SUM(diff_n) s
                          FROM question WHERE kp_name<>'' AND diff_n>0
                          GROUP BY kp_name HAVING s>=50
                          ORDER BY m ASC LIMIT 8"""):
        print(f"   {str(r['kp_name'])[:30]:<32} 题量{r['n']:>3}  全站熟练率 {r['m']*100:5.1f}%")


def cmd_hard(args):
    c = conn()
    print(f"\n全站最难题排行 (样本>={args.min_n}，按熟练率升序)\n")
    print(f"{'qid':>6} {'组':>4} {'年':>6} {'题':>4} {'熟练':>6} {'不熟':>6} {'不会':>6} "
          f"{'熟练率':>7}  知识点")
    hr()
    for r in c.execute("""SELECT * FROM question WHERE diff_n>=?
                          ORDER BY mastery ASC LIMIT ?""", (args.min_n, args.top)):
        print(f"{r['id']:>6} {r['group_name'][-1]:>4} {r['year']:>6} {r['number']:>4} "
              f"{r['happy']:>6} {r['maybe']:>6} {r['sad']:>6} "
              f"{r['mastery']*100:>6.1f}%  {str(r['kp_name'])[:24]}")


def cmd_wrong(args):
    """错题本：按「最近答错时间倒序」列出错过的题。"""
    c = conn()
    where, a = ["last_wrong IS NOT NULL"], []
    if args.group:
        where.append("group_id=?"); a.append(str(args.group))
    if args.kp:
        where.append("kp_name LIKE ?"); a.append(f"%{args.kp}%")
    if args.unsolved_only:
        # 只看「错过但还没答对过」的题（当前 streak 仍为 0）
        where.append("id NOT IN (SELECT qid FROM srs WHERE streak>0)")

    sql = f"""SELECT q.*, r.last_wrong, r.elapsed,
                     (SELECT COUNT(*) FROM record r2
                       WHERE r2.qid=q.id AND r2.ok=0) wrong_times,
                     (SELECT COUNT(*) FROM record r2
                       WHERE r2.qid=q.id AND r2.ok=1) right_times,
                     COALESCE(s.streak,0) streak, s.due
              FROM question q
              JOIN (SELECT qid, MAX(last_wrong) last_wrong, AVG(elapsed) elapsed
                      FROM record WHERE ok=0 GROUP BY qid) r ON r.qid=q.id
              LEFT JOIN srs s ON s.qid=q.id
              WHERE {' AND '.join(where)}
              ORDER BY r.last_wrong DESC LIMIT ?"""
    rows = c.execute(sql, a + [args.top]).fetchall()

    hr("═")
    print(f"错题本  {'(group ' + str(args.group) + ')' if args.group else ''}"
          f"{'  [仅未攻克]' if args.unsolved_only else ''}")
    hr("═")
    if not rows:
        print("\n还没有错题记录 —— 先刷几道，答错的会自动进这里。\n")
        return

    tot_w = c.execute("""SELECT COUNT(DISTINCT qid) FROM record
                         WHERE ok=0""" + (" AND qid IN (SELECT id FROM question WHERE group_id=?)"
                                          if args.group else ""),
                      ([str(args.group)] if args.group else [])).fetchone()[0]
    agg = c.execute("""SELECT COUNT(*) n, SUM(ok=0) w, AVG(elapsed) av
                       FROM record WHERE elapsed IS NOT NULL""" +
                    (" AND qid IN (SELECT id FROM question WHERE group_id=?)"
                     if args.group else ""),
                    ([str(args.group)] if args.group else [])).fetchone()
    print(f"\n累计错题 {tot_w} 道   作答 {agg['n'] or 0} 次 "
          f"(错 {agg['w'] or 0} 次, 错率 {(agg['w'] or 0)/max(1,agg['n'] or 1)*100:.1f}%)"
          f"   平均每题 {fmt_dur(agg['av'])}")
    print()

    import datetime as _dt
    for i, r in enumerate(rows, 1):
        when = _dt.datetime.fromtimestamp(r["last_wrong"]).strftime("%m-%d %H:%M")
        flag = "✅已攻克" if r["streak"] > 0 else "⚠ 未攻克"
        print(f" {i:>2}. [{r['group_name']}] {r['year']}年 第{r['number']}题 "
              f"({r['qtype_name']}{r['score']}分)  {flag}  "
              f"错 {r['wrong_times']}/对 {r['right_times']}  最近 {when}"
              f"{'  均用时 ' + fmt_dur(r['elapsed']) if r['elapsed'] is not None else ''}")
        print(f"     {r['id']}  {r['kp_name'] or '(未分类)'}")
        stem = (r["stem"] or "").replace("\n", " ")
        if stem:
            print(f"     {stem[:110]}{'…' if len(stem) > 110 else ''}")

    print(f"\n提示: 只练错题 → python app/bank.py quiz --group <g> --due")
    print(f"      看未攻克的 → python app/bank.py wrong --unsolved-only\n")


def cmd_export(args):
    out = args.anki
    c = conn()
    rows = c.execute("SELECT * FROM question WHERE gradeable=1 ORDER BY group_id, year").fetchall()
    if out.lower().endswith(".csv"):
        import csv
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["qid", "group", "year", "number", "kp", "answer", "image"])
            for r in rows:
                w.writerow([r["id"], r["group_name"], r["year"], r["number"],
                            r["kp_name"], r["answer"], img_path(r)])
        print(f"已导出 CSV (Anki 可用「导入」直接读): {out}  共 {len(rows)} 题")
        print("提示: Anki 导入时把 image 列映射为图片字段即可显示题图。")
        return

    # 生成 Anki 可导入的 TSV（含 HTML img 标签）
    with open(out, "w", encoding="utf-8") as f:
        f.write("#separator:tab\n#html:true\n")
        f.write("#columns:Front\tBack\tTags\n")
        for r in rows:
            p = img_path(r).replace("\\", "/")
            front = f'<img src="file:///{p}">'
            back = f'{r["answer"]}<br><small>{r["kp_name"]}</small>'
            tags = f'ztq::{r["group_name"]}::{r["year"]}'
            f.write(f"{front}\t{back}\t{tags}\n")
    print(f"已导出 Anki TSV: {out}  共 {len(rows)} 题")


def cmd_paper(args):
    """随机组卷 —— 复用 make_paper 的难度模型，抽题后可直接进入作答。"""
    import random
    c = conn()

    # 结构难度先验（站点互动数据与 LLM 评估都没有时的兜底）
    TYPE_PRIOR = {"选择题": 0.55, "填空题": 0.45, "解答题": 0.32}
    # 注意：hardness() 返回的是「难度」（越大越难），而 BANDS 用「难度」区间。
    # level_of 也必须用同一套阈值，否则 hard 档会抽到 easy 的题。
    BANDS = {"easy": (0.00, 0.38), "mid": (0.38, 0.70), "hard": (0.70, 1.01)}
    MIX = {"easy": 0.3, "mid": 0.4, "hard": 0.3}
    # 真题卷面结构（与 app/index.html 的 PAPER_STRUCT 保持一致，满分均 150）
    STRUCT = {"new": {"选择题": 10, "填空题": 6, "解答题": 6},   # 2021 及以后 = 22 题
              "old": {"选择题": 8,  "填空题": 6, "解答题": 9}}   # 2020 及以前 = 23 题
    STRUCT_ORDER = ("选择题", "填空题", "解答题")

    def hardness(r):
        # 优先级与 app/index.html 的 qHardness() 保持一致
        if r["mastery"] is not None:
            return 1.0 - float(r["mastery"])       # 1) 站点真实熟练率
        try:
            if r["llm_diff"] is not None:
                return max(0.02, min(0.98, float(r["llm_diff"])))   # 2) LLM 评估
        except (KeyError, IndexError, TypeError):
            pass
        h = 1.0 - TYPE_PRIOR.get(r["qtype_name"], 0.5)              # 3) 先验兜底
        try:
            y = int(r["year"])
            h += max(-0.06, min(0.06, (2012 - y) * 0.005))
        except (TypeError, ValueError):
            pass
        return max(0.05, min(0.95, h))

    def level_of(h):
        return "easy" if h < 0.38 else ("mid" if h < 0.70 else "hard")

    rows = pool(c, limit=None)
    if args.groups:
        gset = {str(g) for g in args.groups}
        rows = [r for r in rows if r["group_id"] in gset]
    if args.types:
        rows = [r for r in rows if r["qtype_name"] in args.types]
    if args.kp:
        kws = [k.lower() for k in args.kp]
        rows = [r for r in rows
                if r["kp_name"] and any(k in r["kp_name"].lower() for k in kws)]

    if not rows:
        print("没有匹配的题目，请放宽条件。")
        return

    rng = random.Random(args.seed)

    def pick_by_level(cand, want, relax=True):
        """从 cand 中挑 want 道。

        优先满足难度档；该档不足时（relax=True）放宽到全部 cand 补足，
        保证真卷结构下题量能达到配额。返回 (选中列表, 实际难度命中数)。
        """
        if want <= 0 or not cand:
            return []
        cand = list(cand)
        rng.shuffle(cand)
        if args.level != "mixed":
            lo, hi = BANDS[args.level]
            sub = [r for r in cand if lo <= hardness(r) < hi]
            if not relax:
                return sub[:want]
            # 先取符合档位的，不足则用其他难度的同题型题补满
            picked = sub[:want]
            if len(picked) < want:
                got = {r["id"] for r in picked}
                picked.extend([r for r in cand if r["id"] not in got][: want - len(picked)])
            return picked
        out = []
        for lv, frac in MIX.items():
            w = round(want * frac)
            c2 = [r for r in cand if level_of(hardness(r)) == lv]
            rng.shuffle(c2)
            out.extend(c2[:w])
        if len(out) < want:
            got = {r["id"] for r in out}
            rest = [r for r in cand if r["id"] not in got]
            rng.shuffle(rest)
            out.extend(rest[: want - len(out)])
        return out[:want]

    struct_used = getattr(args, "struct", "off")
    if struct_used == "auto":
        struct_used = "old" if (args.groups and all(str(g).startswith("old") for g in args.groups)) else "new"

    if struct_used != "off":
        # 真题结构：按题型配额抽题，最后按 选择→填空→解答 分段（段内由难到易）
        picked = []
        quota = STRUCT[struct_used]
        short = {}
        for tp in STRUCT_ORDER:
            want = quota.get(tp, 0)
            if not want:
                continue
            cand = [r for r in rows if r["qtype_name"] == tp]
            got = pick_by_level(cand, want)
            if len(got) < want:
                short[tp] = (len(got), want, len(cand))
            picked.extend(got)
        if short:
            print("⚠ 以下题型题量不足（题库中该题型总数不够）：")
            for tp, (g, w, n) in short.items():
                print(f"    {tp}: 抽到 {g}/{w} 道（全库该题型仅 {n} 道）")
        picked = [r for tp in STRUCT_ORDER
                  for r in sorted([x for x in picked if x["qtype_name"] == tp],
                                  key=hardness, reverse=True)]
    elif args.level == "mixed":
        picked = pick_by_level(rows, args.n)
    else:
        lo, hi = BANDS[args.level]
        cand = [r for r in rows if lo <= hardness(r) < hi] or rows
        rng.shuffle(cand)
        picked = cand[: args.n]

    print(f"\n{'='*70}")
    print(f"随机卷  {len(picked)} 题   难度档={args.level}"
          + (f"   结构={STRUCT[struct_used]}" if struct_used != "off" else "")
          + (f"   知识点={'、'.join(args.kp)}" if args.kp else ""))
    print('=' * 70)
    total = 0
    for i, r in enumerate(picked, 1):
        h = hardness(r)
        total += r["score"] or 0
        p_src = "站点" if r["mastery"] is not None else (
            "LLM" if r["llm_diff"] is not None else "先验")
        setname = f"[{r['paper_set']}]" if r["paper_set"] else ""
        print(f"{i:>2}. [{r['qtype_name']} {r['score']}分] {r['group_id']} "
              f"{r['year']}{setname}#{r['number']}"
              f"  难度{h:.2f}({level_of(h)})[{p_src}]  {r['kp_name'] or '未分类'}")
    dist = {}
    for r in picked:
        k = level_of(hardness(r))
        dist[k] = dist.get(k, 0) + 1
    print('-' * 70)
    print(f"总分 {total} 分   难度分布 {dist}")

    if not args.quiz:
        print("\n（加 --quiz 直接开始作答）")
        return

    print("\n开始作答: 输入 A/B/C/D  |  d=看答案  s=跳过  q=退出\n")
    right = wrong = skip = 0
    for i, r in enumerate(picked, 1):
        show_question(r, reveal=False, idx=i)
        if not r["gradeable"]:
            ans = r["answer"] or ""
            if ans:
                print("\n  请思考后回车看答案，输入 q 退出: ", end="", flush=True)
                if input().strip().lower() == "q":
                    break
                print(f"  → 参考答案: {ans}")
            else:
                print("\n  本题暂无答案，回车继续，输入 q 退出: ", end="", flush=True)
                if input().strip().lower() == "q":
                    break
            skip += 1
            print()
            continue
        print("\n  请选择 [A/B/C/D]: ", end="", flush=True)
        v = input().strip()
        if v.lower() == "q":
            break
        if v.lower() in ("s", ""):
            skip += 1
            print("  → 跳过\n")
            continue
        if v.lower() == "d":
            print(f"  → 答案: {r['answer']}\n")
            skip += 1
            continue
        key = parse_multi(r["answer"])
        ok = 1 if (parse_multi(v) and parse_multi(v) == key) else 0
        elapsed = time.time() - q_t0
        iv, ease, streak = srs_update(c, r["id"], ok)
        log(c, r["id"], "answer", v.upper(), ok, elapsed=elapsed)
        if ok:
            right += 1
            print(f"  ✅ 正确（答案 {r['answer']}）  连对{streak}  "
                  f"用时 {fmt_dur(elapsed)}  下次 {iv:.1f} 天后\n")
        else:
            wrong += 1
            print(f"  ❌ 错误（你选 {v.upper()}，正确 {r['answer']}）  "
                  f"用时 {fmt_dur(elapsed)}  ease→{ease:.2f}\n")

    print(f"\n{'─'*40}\n本卷结束: 对 {right} / 错 {wrong} / 跳过 {skip}"
          f"   正确率 {right/max(1,right+wrong)*100:.1f}%")


def cmd_serve(args):
    """零依赖本地浏览页。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.parse

    idx = os.path.join(APP, "index.html")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            p = u.path
            if p in ("/", "/index.html"):
                try:
                    with open(idx, "rb") as f:
                        body = f.read()
                except FileNotFoundError:
                    body = b"index.html not found"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if p == "/api/index.json":
                fp = INDEX_JSON
                if not os.path.exists(fp):
                    # 骨架版 / 数据未导入时给出空索引，而不是 500，
                    # 这样页面能正常打开并显示「空题库」而不是白屏。
                    body = json.dumps({
                        "counts": {"question": 0, "site": 0, "old": 0, "mock": 0,
                                   "gradeable": 0, "with_answer": 0,
                                   "local_answer": 0, "kp_covered": 0,
                                   "kp": 0, "paper": 0},
                        "groups": {}, "papers": [], "mock_papers": [],
                        "old_papers": [], "knowledge_points": [], "questions": [],
                        "answer_sources": {},
                        "meta": {"skeleton": True,
                                 "note": "index.json 缺失，返回空索引；请先导入数据"},
                    }, ensure_ascii=False).encode("utf-8")
                else:
                    with open(fp, "rb") as f:
                        body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if p == "/api/progress":
                cc = sqlite3.connect(DB)
                cc.row_factory = sqlite3.Row
                try:
                    per_q = {}
                    for s in cc.execute("SELECT * FROM srs WHERE seen>0"):
                        hist = [{"t": datetime.fromtimestamp(h["ts"]).strftime("%m-%d %H:%M"),
                                 "value": h["value"], "ok": h["ok"]}
                                for h in cc.execute(
                                    "SELECT ts,value,ok FROM record WHERE qid=? ORDER BY ts DESC LIMIT 8",
                                    (s["qid"],))]
                        per_q[s["qid"]] = {
                            "seen": s["seen"], "right": s["right_cnt"],
                            "wrong": s["wrong_cnt"], "streak": s["streak"],
                            "ease": s["ease"],
                            "due_in": (s["due"] - time.time()) / 86400.0,
                            "hist": hist,
                        }
                    tot = cc.execute(
                        "SELECT COUNT(*) a, COALESCE(SUM(right_cnt),0) r,"
                        " COALESCE(SUM(wrong_cnt),0) w FROM srs").fetchone()
                    due = cc.execute("SELECT COUNT(*) FROM srs WHERE due<=?",
                                     (time.time(),)).fetchone()[0]
                    self._json({"per_q": per_q, "total_answers": tot["a"],
                                "total_right": tot["r"], "total_wrong": tot["w"],
                                "due": due})
                finally:
                    cc.close()
                return
            if p == "/api/wrong":
                # 错题本：按最近答错时间倒序
                cc = sqlite3.connect(DB)
                cc.row_factory = sqlite3.Row
                migrate(cc)
                try:
                    limit = 300
                    rows = cc.execute("""
                        SELECT q.id, q.group_id, q.group_name, q.year, q.number,
                               q.qtype_name, q.score, q.kp_name, q.answer, q.stem,
                               r.last_wrong, AVG(r.elapsed) el,
                               COUNT(*) wtimes
                          FROM question q
                          JOIN record r ON r.qid = q.id AND r.ok = 0
                         GROUP BY q.id
                         ORDER BY r.last_wrong DESC
                         LIMIT ?""", (limit,)).fetchall()
                    # 每题的对错计数与 SRS 状态
                    srs = {s["qid"]: s for s in cc.execute(
                        "SELECT qid,right_cnt,wrong_cnt,streak FROM srs")}
                    out = []
                    for r in rows:
                        s = srs.get(r["id"])
                        out.append({
                            "id": r["id"], "group": r["group_id"],
                            "group_name": r["group_name"], "year": r["year"],
                            "num": r["number"], "type": r["qtype_name"],
                            "score": r["score"], "kp": r["kp_name"],
                            "answer": r["answer"] or "", "stem": r["stem"] or "",
                            "last_wrong": r["last_wrong"],
                            "when": datetime.fromtimestamp(r["last_wrong"]).strftime("%m-%d %H:%M"),
                            "elapsed": r["el"], "wtimes": r["wtimes"],
                            "right": (s["right_cnt"] if s else 0),
                            "streak": (s["streak"] if s else 0),
                            "solved": bool(s and s["streak"] > 0),
                        })
                    # 耗时统计
                    agg = cc.execute("""SELECT COUNT(*) n, COALESCE(SUM(ok=0),0) w,
                                               AVG(elapsed) av
                                          FROM record WHERE elapsed IS NOT NULL""").fetchone()
                finally:
                    cc.close()
                self._json({"items": out,
                            "answers": agg["n"] or 0, "wrong_answers": agg["w"] or 0,
                            "avg_elapsed": agg["av"]})
                return
            if p == "/api/due":
                cc = sqlite3.connect(DB)
                try:
                    ids = [r[0] for r in cc.execute(
                        "SELECT qid FROM srs WHERE due<=? ORDER BY due ASC LIMIT 200",
                        (time.time(),))]
                finally:
                    cc.close()
                self._json(ids)
                return
            if p.startswith("/vendor/"):
                # 本地静态资源（KaTeX 等），只读且限定在 APP/vendor 内
                VROOT = VENDOR
                fp = os.path.join(VROOT, p[len("/vendor/"):].replace("/", os.sep))
                fp = os.path.normpath(fp)
                if not fp.startswith(os.path.normpath(VROOT)) or not os.path.isfile(fp):
                    self.send_error(404); return
                ext = os.path.splitext(fp)[1].lower()
                ctype = {".js": "application/javascript; charset=utf-8",
                         ".css": "text/css; charset=utf-8",
                         ".woff2": "font/woff2",
                         ".woff": "font/woff",
                         ".ttf": "font/ttf",
                         ".json": "application/json; charset=utf-8"}.get(
                             ext, "application/octet-stream")
                with open(fp, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=604800")
                self.end_headers()
                self.wfile.write(body)
                return
            if p.startswith("/images/"):
                fp = os.path.join(IMGROOT, p[len("/images/"):].replace("/", os.sep))
                fp = os.path.normpath(fp)
                if not fp.startswith(os.path.normpath(IMGROOT)) or not os.path.isfile(fp):
                    self.send_error(404); return
                with open(fp, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/api/cleanup_test":
                # 仅供自动化回归脚本清理自己造的数据（错题本/计时测试用）
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(n).decode("utf-8"))
                    qids = [str(x) for x in (payload.get("qids") or [])]
                except Exception as e:
                    self._json({"ok": False, "error": str(e)}, 400); return
                if not qids:
                    self._json({"ok": False, "error": "no qids"}, 400); return
                cc = sqlite3.connect(DB)
                migrate(cc)
                try:
                    ph = ",".join("?" * len(qids))
                    r1 = cc.execute(f"DELETE FROM record WHERE qid IN ({ph}) AND note='web'", qids)
                    r2 = cc.execute(f"DELETE FROM srs WHERE qid IN ({ph})", qids)
                    cc.commit()
                    self._json({"ok": True, "record": r1.rowcount, "srs": r2.rowcount})
                finally:
                    cc.close()
                return
            if self.path != "/api/record":
                self.send_error(404); return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n).decode("utf-8"))
                qid = str(payload["qid"])
                ok = int(payload.get("ok") or 0)
                value = str(payload.get("value") or "")
                # 前端上报的作答耗时（毫秒），转秒；缺省 None 表示未计时
                ms = payload.get("ms")
                elapsed = (float(ms) / 1000.0) if isinstance(ms, (int, float)) and ms >= 0 else None
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400); return

            cc = sqlite3.connect(DB)
            cc.row_factory = sqlite3.Row
            migrate(cc)
            try:
                r = cc.execute("SELECT * FROM question WHERE id=?", (qid,)).fetchone()
                if not r:
                    self._json({"ok": False, "error": "unknown qid"}, 404); return
                iv, ease, streak = srs_update(cc, qid, ok)
                log(cc, qid, "answer" if r["gradeable"] else "self", value, ok,
                    note="web", elapsed=elapsed)
                self._json({"ok": True, "interval": iv, "ease": ease, "streak": streak,
                            "elapsed": elapsed})
            finally:
                cc.close()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    print(f"本地题库浏览页已启动: http://127.0.0.1:{args.port}/")
    print("按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


def main():
    ap = argparse.ArgumentParser(description="本地题库刷题客户端（纯离线）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_pool(p):
        p.add_argument("--group", help="8=数学一（可加 9/10 若已导入）或 mock2024/mock2025/mock2026 / old2005")
        p.add_argument("--year", help="年份，如 2025")
        p.add_argument("--kp", help="知识点关键词，如 微分中值定理")
        p.add_argument("--hard", action="store_true", help="全站难题优先")

    p = sub.add_parser("list", help="列出题目")
    add_pool(p)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--gradeable", action="store_true", help="只看有答案的")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="查看题目详情")
    p.add_argument("qid")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("quiz", help="刷题")
    add_pool(p)
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--due", action="store_true", help="只看到期复习")
    p.add_argument("--new", action="store_true", help="只看没做过的新题")
    p.add_argument("--any-type", action="store_true", help="包含未收录答案的题(自评)")
    p.set_defaults(func=cmd_quiz)

    p = sub.add_parser("review", help="到期复习队列")
    p.add_argument("-n", type=int, default=50)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("stats", help="统计报告")
    p.add_argument("--group")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("weak", help="薄弱知识点")
    p.add_argument("--top", type=int, default=20)
    p.set_defaults(func=cmd_weak)

    p = sub.add_parser("hard", help="全站最难题")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-n", type=int, default=50)
    p.set_defaults(func=cmd_hard)

    p = sub.add_parser("wrong", help="错题本（按最近答错时间倒序）")
    p.add_argument("--group", help="只看某组，如 8 或 mock2024")
    p.add_argument("--kp", help="知识点关键词过滤")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--unsolved-only", action="store_true",
                   help="只看错过但还没答对过的题")
    p.set_defaults(func=cmd_wrong)

    p = sub.add_parser("export", help="导出 Anki")
    p.add_argument("--anki", required=True, help="输出 .tsv 或 .csv")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("paper", help="随机组卷（可按难度/知识点）")
    p.add_argument("-n", type=int, default=22, help="题量")
    p.add_argument("--level", default="mixed",
                   choices=["easy", "mid", "hard", "mixed"], help="难度档")
    p.add_argument("--groups", nargs="*", default=None,
                   help="来源组，如 8 9 10 或 old2005 mock2024")
    p.add_argument("--kp", nargs="*", default=None, help="知识点关键词（可多个）")
    p.add_argument("--types", nargs="*", default=None,
                   help="题型：选择题 填空题 解答题")
    p.add_argument("--seed", type=int, default=None, help="随机种子")
    p.add_argument("--struct", default="off", choices=["off", "auto", "new", "old"],
                   help="卷面结构：off=仅按难度 / auto=按范围自动 / "
                        "new=10选+6填+6解 / old=8选+6填+9解")
    p.add_argument("--quiz", action="store_true", help="生成后立即开始作答")
    p.set_defaults(func=cmd_paper)

    p = sub.add_parser("serve", help="本地浏览网页")
    p.add_argument("--port", type=int, default=8899)
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
