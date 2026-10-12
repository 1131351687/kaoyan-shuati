# -*- coding: utf-8 -*-
"""
ztq.py —— 真题强(https://zhentiqiang.com) 题库抓取 / 本地刷题记录工具

已还原的接口（来自 /static/js/index.js 静态分析）：
  GET  /api/bootstrap?group_id=<id>            试卷组 + 知识点树 + 试卷列表 + 题目列表
  GET  /api/knowledge_tree/<group_id>          知识点树
  GET  /api/papers/<group_id>                  试卷列表
  GET  /api/question_info/<qid>                单题完整信息（含答案/解析）
  GET  /api/question_stats/<qid>               单题统计
  POST /api/questions/batch   {question_ids:[]} 批量取题完整信息
  GET  /api/check_login                        登录态
  POST /api/student_interaction {question_id, interaction_type}   mood_happy/mood_maybe/mood_sad → 6/7/8
  GET  /api/user_selection/<qid>               当前用户对该题的选择
  GET  /api/user_collection/<type>?group_id=&page=&per_page=
  GET  /api/user_mood_records                  我的全部记录
  POST /api/comments {question_id, content}    评论（含解析讨论）
  GET  /api/comments/<qid>                     评论列表
  POST /api/question_issue_report              报错
  GET  /api/papergroup_stats/<group_id>        试卷组统计
  POST /api/book_purchase_click
无任何 CSRF/sign 头，纯 Cookie 会话 —— 不需要签名算法。

用法:
  python ztq.py probe                       # 探测所有接口，看哪些能匿名访问
  python ztq.py dump  --group 8             # 按试卷组抓全量题
  python ztq.py dump  --group 8 --with-answers
  python ztq.py quiz                        # 刷题 + 自动记录 mood 到服务器
  python ztq.py stats
"""
import os
import re
import ssl
import sys
import json
import time
import sqlite3
import argparse
import urllib.request
import urllib.parse
import urllib.error
from http.cookiejar import CookieJar

from paths import IMAGES, ZTQ_DB

BASE = "https://zhentiqiang.com"
ROOT = os.path.dirname(os.path.abspath(__file__))
DB = ZTQ_DB                 # data/ztq.db（从站点抓的原始库）
IMG = IMAGES                # data/images/
os.makedirs(IMG, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

MOOD = {"happy": 6, "maybe": 7, "sad": 8, "empty": 7}


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    try:
        c.set_ciphers("DEFAULT@SECLEVEL=1")
    except Exception:
        pass
    return c


CJ = CookieJar()
OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(CJ),
    urllib.request.HTTPSHandler(context=_ctx()),
)


def req(path, method="GET", payload=None, ref=None, timeout=25, raw=False):
    url = path if path.startswith("http") else BASE + path
    data = None
    hd = {
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": ref or (BASE + "/kaoyan/math"),
    }
    if payload is not None:
        data = json.dumps(payload).encode()
        hd["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=hd, method=method)
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            body = resp.read()
            if raw:
                return resp.status, body, dict(resp.headers)
            try:
                return resp.status, json.loads(body.decode("utf-8", "ignore")), dict(resp.headers)
            except Exception:
                return resp.status, body.decode("utf-8", "ignore"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "ignore") if not raw else e.read()), dict(e.headers)
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}", {}


# ---------------------------------------------------------------- DB

def db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS question(
        id TEXT PRIMARY KEY, group_id TEXT, paper_name TEXT, year TEXT,
        number TEXT, qtype INT, score INT, image_url TEXT, full_image TEXT,
        answer TEXT, analysis TEXT, raw TEXT, fetched REAL);
    CREATE TABLE IF NOT EXISTS record(
        id INTEGER PRIMARY KEY AUTOINCREMENT, qid TEXT, ts REAL,
        action TEXT, value TEXT, ok INT, note TEXT);
    CREATE TABLE IF NOT EXISTS srs(
        qid TEXT PRIMARY KEY, seen INT DEFAULT 0, right_cnt INT DEFAULT 0,
        wrong_cnt INT DEFAULT 0, streak INT DEFAULT 0, ease REAL DEFAULT 2.5,
        last_ts REAL, due REAL DEFAULT 0);
    """)
    c.commit()
    return c


def save_questions(c, group_id, items, paper_name="", answered=None):
    n = 0
    for q in items:
        qid = str(q.get("id") or q.get("question_id") or "")
        if not qid:
            continue
        img = q.get("image_url") or q.get("image") or ""
        full = img.replace("_thumb.png", ".png") if img else ""
        c.execute("""INSERT INTO question(id,group_id,paper_name,year,number,qtype,score,
                     image_url,full_image,answer,analysis,raw,fetched)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET
                       group_id=excluded.group_id,
                       paper_name=COALESCE(NULLIF(excluded.paper_name,''),question.paper_name),
                       year=COALESCE(excluded.year,question.year),
                       number=COALESCE(excluded.number,question.number),
                       qtype=excluded.qtype, score=excluded.score,
                       image_url=COALESCE(NULLIF(excluded.image_url,''),question.image_url),
                       full_image=COALESCE(NULLIF(excluded.full_image,''),question.full_image),
                       answer=COALESCE(NULLIF(excluded.answer,''),question.answer),
                       analysis=COALESCE(NULLIF(excluded.analysis,''),question.analysis),
                       raw=excluded.raw, fetched=excluded.fetched""",
                  (qid, str(group_id), q.get("paper_name") or paper_name,
                   q.get("year"), str(q.get("question_number") or q.get("number") or ""),
                   q.get("question_type") or q.get("type") or 1,
                   q.get("score") or 0, img, full,
                   q.get("answer") or q.get("correct_answer") or "",
                   q.get("analysis") or q.get("explanation") or q.get("solution") or "",
                   json.dumps(q, ensure_ascii=False), time.time()))
        n += 1
    c.commit()
    return n


def log(c, qid, action, value="", ok=None, note=""):
    c.execute("INSERT INTO record(qid,ts,action,value,ok,note) VALUES(?,?,?,?,?,?)",
              (qid, time.time(), action, value, None if ok is None else int(ok), note))
    c.commit()


# ---------------------------------------------------------------- 抓取

def probe():
    """对每个已知接口打一次，回报真实响应，确认可用面。"""
    tests = [
        ("GET", "/api/bootstrap?group_id=8", None),
        ("GET", "/api/bootstrap", None),
        ("GET", "/api/knowledge_tree/8", None),
        ("GET", "/api/papers/8", None),
        ("GET", "/api/papergroup_stats/8", None),
        ("GET", "/api/check_login", None),
        ("GET", "/api/question_info/1", None),
        ("GET", "/api/question_stats/1", None),
        ("GET", "/api/user_mood_records", None),
        ("POST", "/api/questions/batch", {"question_ids": ["1", "2", "3"]}),
        ("POST", "/api/student_interaction", {"question_id": 1, "interaction_type": "mood_happy"}),
    ]
    print(f"{'METHOD':6} {'PATH':42} {'ST':>4}  RESULT")
    print("-" * 100)
    for m, p, body in tests:
        st, data, _ = req(p, method=m, payload=body)
        if isinstance(data, dict):
            keys = list(data.keys())[:9]
            extra = f"success={data.get('success')}" if "success" in data else ""
            n = ""
            for k in ("questions", "papers", "papergroups", "knowledge_tree", "data", "stats"):
                if isinstance(data.get(k), list):
                    n += f" {k}[{len(data[k])}]"
            desc = f"json keys={keys} {extra}{n}"
        else:
            desc = re.sub(r"\s+", " ", str(data))[:150]
        print(f"{m:6} {p:42} {st:>4}  {desc}")
        time.sleep(0.35)


def dump(group_id, with_answers=False, delay=0.4, max_paper=None):
    c = db()
    print(f"[*] group_id={group_id} 拉 bootstrap ...")
    st, boot, _ = req(f"/api/bootstrap?group_id={group_id}")
    if st != 200 or not isinstance(boot, dict):
        print("  ! bootstrap 失败:", st, str(boot)[:200])
        return
    print("  bootstrap 顶层键:", list(boot.keys()))

    papers = boot.get("papers") or []
    print(f"  试卷 {len(papers)} 份, 知识点组 {len(boot.get('papergroups') or [])} 个")

    # bootstrap 内的 papergroups 通常只带元数据，题目在各 paper 的 questions 里
    collected = 0
    paper_qids = {}
    for i, p in enumerate(papers):
        if max_paper and i >= max_paper:
            break
        name = p.get("name") or p.get("paper_name") or f"paper{p.get('id')}"
        qs = p.get("questions") or []
        if not qs and p.get("id"):
            st2, d2 = req(f"/api/papers/{p['id']}")
            if isinstance(d2, dict):
                qs = d2.get("questions") or []
        for q in qs:
            q["paper_name"] = name
            q.setdefault("year", p.get("year"))
        collected += save_questions(c, group_id, qs, name)
        paper_qids[name] = [str(q.get("id") or q.get("question_id")) for q in qs]
        print(f"  [{i+1}/{len(papers)}] {name}: {len(qs)} 题")
        time.sleep(delay)

    if not papers:
        # 退路：直接从页面 HTML 里抠 data-question-id
        st, html, _ = req(f"/kaoyan/math?group_id={group_id}", raw=True)
        ids = sorted(set(re.findall(rb'data-question-id="(\d+)"', html if isinstance(html, bytes) else html.encode())))
        print(f"  ! bootstrap 无 papers，改从 HTML 抠出 {len(ids)} 个题目 id")
        collected = len(ids)
        paper_qids["__html__"] = [i.decode() for i in ids]

    # 批量补全题目详情（含答案/解析）
    all_ids = [q for v in paper_qids.values() for q in v]
    print(f"[*] 共 {len(set(all_ids))} 个唯一题目 id，批量拉详情 ...")
    uniq = list(dict.fromkeys(all_ids))
    for i in range(0, len(uniq), 50):
        chunk = uniq[i:i + 50]
        st, d, _ = req("/api/questions/batch", method="POST", payload={"question_ids": chunk})
        got = 0
        if isinstance(d, dict) and d.get("questions"):
            got = save_questions(c, group_id, d["questions"])
        if not got and with_answers:
            for qid in chunk:
                st2, q2, _ = req(f"/api/question_info/{qid}")
                if isinstance(q2, dict):
                    save_questions(c, group_id, [q2])
                    got += 1
                time.sleep(delay)
        print(f"  batch {i//50+1}: {len(chunk)} 请求 -> {got} 入库 (HTTP {st})")
        time.sleep(delay)

    tot = c.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    withans = c.execute("SELECT COUNT(*) FROM question WHERE answer<>''").fetchone()[0]
    print(f"[✓] 题库共 {tot} 题，其中 {withans} 题已带答案")
    print(f"[✓] DB: {DB}")
    log(c, "-", "dump", f"group={group_id} total={tot}", 1)


# ---------------------------------------------------------------- 刷题

def srs_next(c, n=20):
    rows = c.execute("""
        SELECT q.id, q.paper_name, q.number, q.qtype, q.full_image, q.image_url,
               q.answer, q.analysis, COALESCE(s.wrong_cnt,0) w, COALESCE(s.seen,0) s
        FROM question q LEFT JOIN srs s ON s.qid=q.id
        WHERE COALESCE(s.due,0) <= ?
        ORDER BY w DESC, RANDOM() LIMIT ?""", (time.time(), n)).fetchall()
    return rows


def srs_update(c, qid, ok):
    r = c.execute("SELECT seen,right_cnt,wrong_cnt,streak,ease FROM srs WHERE qid=?", (qid,)).fetchone()
    if r:
        seen, right, wrong, streak, ease = r
    else:
        seen = right = wrong = streak = 0
        ease = 2.5
    seen += 1
    if ok:
        right += 1
        streak += 1
        ease = min(3.0, ease + 0.1)
    else:
        wrong += 1
        streak = 0
        ease = max(1.3, ease - 0.3)
    steps = [0, 1, 3, 7, 15, 30, 60]
    days = steps[min(streak, len(steps) - 1)] if ok else 0
    c.execute("""INSERT INTO srs(qid,seen,right_cnt,wrong_cnt,streak,ease,last_ts,due)
                 VALUES(?,?,?,?,?,?,?,?)
                 ON CONFLICT(qid) DO UPDATE SET seen=?,right_cnt=?,wrong_cnt=?,streak=?,
                 ease=?,last_ts=?,due=?""",
              (qid, seen, right, wrong, streak, ease, time.time(), time.time() + days * 86400,
               seen, right, wrong, streak, ease, time.time(), time.time() + days * 86400))
    c.commit()


def sync_mood(qid, mood, do=True):
    """把作答结果写回站点（happy=熟练 maybe=不熟 sad=不会），构造刷题记录。"""
    if not do:
        return None
    st, d, _ = req("/api/student_interaction", method="POST",
                   payload={"question_id": qid, "interaction_type": f"mood_{mood}"})
    ok = isinstance(d, dict) and d.get("success")
    return f"HTTP{st} {'OK' if ok else (d.get('message') if isinstance(d,dict) else str(d)[:60])}"


def quiz(n=20, sync=False):
    c = db()
    rows = srs_next(c, n)
    if not rows:
        print("没有到期题目，先跑 `python ztq.py dump --group 8`")
        return
    score = 0
    for i, (qid, paper, num, qt, full, thumb, ans, ana, w, s) in enumerate(rows, 1):
        print("\n" + "=" * 78)
        print(f"[{i}/{len(rows)}] {paper} 第{num}题  ({'已错%d次' % w if w else '新题'})")
        link = full or thumb
        if link:
            u = link if link.startswith("http") else BASE + link
            print("  题图:", u)
            local = os.path.join(IMG, f"{qid}.png")
            if not os.path.exists(local):
                st, body, _ = req(u, raw=True)
                if st == 200 and isinstance(body, bytes):
                    open(local, "wb").write(body)
                    print("  已下载:", local)
        key = (ans or "").strip().upper()
        gradeable = key in ("A", "B", "C", "D")
        if gradeable:
            hint = "[A/B/C/D 作答 | a 看答案 | q 跳过 | x 退出]"
        else:
            hint = "[h 熟练/m 不熟/s 不会 | q 跳过 | x 退出]（本题无标准答案，自评）"
        print("  " + hint)
        while True:
            v = input("  > ").strip().lower()
            if v in ("a", "answer"):
                print("  答案:", key or "(本题站点未存标准答案，非选择题/解答题)")
                if ana:
                    print("  解析:", ana[:400])
                continue
            break
        if v in ("x", "exit"):
            break
        if v in ("q", "skip", ""):
            continue

        if gradeable:
            # 客观判分：与还原出的答案键比对
            ok = 1 if v.upper() == key else 0
            mood = "happy" if ok else "sad"
            verdict = f"你选 {v.upper()} / 正确 {key}"
        else:
            # 无答案键的解答题：只能自评
            mood = {"h": "happy", "m": "maybe", "s": "sad"}.get(v, "maybe")
            ok = 1 if mood == "happy" else 0
            verdict = f"自评 {mood}"

        score += ok
        r = sync_mood(qid, mood, sync)
        srs_update(c, qid, ok)
        log(c, qid, "answer", v.upper() if gradeable else mood, ok, note=(r or "local-only"))
        msg = f"[已同步 {r}]" if sync else "[仅本地]"
        print(f"  -> {'✅' if ok else '❌'} {verdict}  {msg}")
    print(f"\n本轮 {score}/{len(rows)}   错题总计:",
          c.execute("SELECT COUNT(*) FROM srs WHERE wrong_cnt>0").fetchone()[0])


def stats():
    c = db()
    print("题目总数:", c.execute("SELECT COUNT(*) FROM question").fetchone()[0])
    print("带答案  :", c.execute("SELECT COUNT(*) FROM question WHERE answer<>''").fetchone()[0])
    print("\n按试卷:")
    for r in c.execute("""SELECT COALESCE(NULLIF(paper_name,''),'(未标注)') p, COUNT(*)
                          FROM question GROUP BY p ORDER BY 2 DESC LIMIT 30"""):
        print(f"  {r[0][:50]:52} {r[1]:>5}")
    print("\n掌握度:")
    for r in c.execute("""SELECT
            SUM(seen=0), SUM(wrong_cnt>0), SUM(wrong_cnt=0 AND seen>0),
            COALESCE(SUM(right_cnt),0), COALESCE(SUM(wrong_cnt),0) FROM srs"""):
        print(f"  未做={r[0] or 0}  有错题={r[1] or 0}  全对={r[2] or 0}  "
              f"总对={r[3]} 总错={r[4]}")
    print("\n到期复习:", c.execute("SELECT COUNT(*) FROM srs WHERE due<=?", (time.time(),)).fetchone()[0])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    d = sub.add_parser("dump")
    d.add_argument("--group", default="8")
    d.add_argument("--with-answers", action="store_true")
    d.add_argument("--delay", type=float, default=0.4)
    d.add_argument("--max-paper", type=int, default=None)
    q = sub.add_parser("quiz")
    q.add_argument("-n", type=int, default=20)
    q.add_argument("--sync", action="store_true", help="把作答同步回站点(需已登录 Cookie)")
    sub.add_parser("stats")
    a = ap.parse_args()

    if a.cmd == "probe":
        probe()
    elif a.cmd == "dump":
        dump(a.group, a.with_answers, a.delay, a.max_paper)
    elif a.cmd == "quiz":
        quiz(a.n, a.sync)
    elif a.cmd == "stats":
        stats()


if __name__ == "__main__":
    main()
