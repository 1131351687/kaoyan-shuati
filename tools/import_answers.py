# -*- coding: utf-8 -*-
"""回填答案到 D:\\try\\bank\\ztq_bank.db（仅 group 8 数学一）。

设计决定
--------
题库原有的 `gradeable` 只表示「A/B/C/D 可机器判分」。
本次回填的 102 条里有 95 条是填空题（LaTeX 表达式）与 7 条解答题，
它们**不是单选**，拿字母比对会永远判错。

因此：
  - `answer`      写入完整答案文本（LaTeX 原样保留）
  - `answer_letters` 若答案为纯字母则写字母，否则留空
  - `gradeable`   保持/设为 0 —— 不进入自动判分，但 UI 可展示
  - 新增来源标记，便于回溯与撤销

新增两列（若不存在）：
  answer_src   'site' | 'local'
  answer_note  人类可读的来源说明（如 "2019年解析.md #12"）

用法：
  python import_answers.py --dry    # 只报告，不写库
  python import_answers.py          # 执行写入
  python import_answers.py --undo   # 撤销本次回填
"""
import os
import re
import sys
import json
import sqlite3
from paths import ANSWERS_RAW, BANK_DB, SOLUTIONS_RAW, TOOLS

BANK = BANK_DB
RAW = ANSWERS_RAW
SOL = SOLUTIONS_RAW
TARGET_GROUP = "8"
# 解答题结论只有 high 置信度才导入；mid 是"末尾公式"，容易是中间步骤，丢弃。
SOL_CONF_OK = {"high"}


def letter_of(s):
    s = s.strip()
    for a, b in (("（", "("), ("）", ")"), ("Ａ", "A"), ("Ｂ", "B"),
                 ("Ｃ", "C"), ("Ｄ", "D")):
        s = s.replace(a, b)
    s = s.rstrip(".．。 ")
    m = re.fullmatch(r"\(?\s*([ABCD])\s*\)?", s)
    return m.group(1) if m else None


def ensure_columns(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    added = []
    if "answer_src" not in cols:
        c.execute("ALTER TABLE question ADD COLUMN answer_src TEXT")
        added.append("answer_src")
    if "answer_note" not in cols:
        c.execute("ALTER TABLE question ADD COLUMN answer_note TEXT")
        added.append("answer_note")
    return added


def load_solutions():
    """
    读解答题结论，只保留 high 置信度。
    返回 {(year, num): (answer_text, note)}
    """
    if not os.path.exists(SOL):
        return {}
    raw = json.load(open(SOL, encoding="utf-8"))
    out = {}
    for y, items in raw.items():
        try:
            yi = int(y)
        except ValueError:
            continue
        for n, v in items.items():
            if v.get("conf") not in SOL_CONF_OK:
                continue
            ans = (v.get("ans") or "").strip()
            if not ans:
                continue
            out[(yi, int(n))] = (ans, f"{y}年解析.md #{n}（解答题结论）")
    return out


def build_plan(c, all_ans, sols):
    rows = c.execute(
        "SELECT id, year, number, qtype_name, answer, gradeable FROM question "
        "WHERE group_id=?", (TARGET_GROUP,)).fetchall()
    plan = []
    for r in rows:
        if r["gradeable"] or (r["answer"] or "").strip():
            continue                       # 已有答案，不动
        y = int(r["year"]) if str(r["year"]).isdigit() else 0
        n = int(r["number"]) if str(r["number"]).isdigit() else None
        if not n:
            continue

        # _answers_raw 是「按题型分桶」结构 {year: {kind: {num: ans}}}，
        # kind ∈ {choice, blank, solve}；老卷小节重编号就是靠这个隔离的。
        kind = {"选择题": "choice", "填空题": "blank",
                "解答题": "solve"}.get(r["qtype_name"], "choice")

        # 优先用填空题/选择题的【答案】；没有则用解答题的【解】结论
        txt, note = "", ""
        per_year = all_ans.get(y) or {}
        bucket = per_year.get(kind) or {}
        if str(n) in bucket:
            txt = (bucket[str(n)] or "").strip()
            note = f"{y}年解析.md {r['qtype_name']}#{n}" if y != 2025 \
                else f"2025年真题.md {r['qtype_name']}#{n}"
        elif (y, n) in sols and r["qtype_name"] == "解答题":
            txt, note = sols[(y, n)]
        elif (y, n) in sols and kind == "solve":
            txt, note = sols[(y, n)]

        if not txt:
            continue
        plan.append({
            "id": r["id"], "year": y, "num": n,
            "qtype": r["qtype_name"], "answer": txt,
            "letter": letter_of(txt),
            "src": "local",
            "note": note,
        })
    return plan


def main():
    dry = "--dry" in sys.argv
    undo = "--undo" in sys.argv

    c = sqlite3.connect(BANK)
    c.row_factory = sqlite3.Row

    if undo:
        added = ensure_columns(c)
        n = c.execute("SELECT COUNT(*) FROM question WHERE answer_src='local'").fetchone()[0]
        c.execute("UPDATE question SET answer='', answer_src=NULL, answer_note=NULL "
                  "WHERE answer_src='local'")
        c.commit()
        print(f"已撤销 {n} 条本地回填答案。")
        return

    # _answers_raw.json 的结构是 {year: {kind: {num: ans}}}（按题型分桶）
    all_ans = {}
    for k, v in json.load(open(RAW, encoding="utf-8")).items():
        y = int(k)
        all_ans[y] = {}
        for kind, bucket in v.items():
            all_ans[y][kind] = {str(a): b for a, b in bucket.items()}
    sols = load_solutions()
    print(f"解答题结论(high) 可用 {len(sols)} 条")
    plan = build_plan(c, all_ans, sols)

    print(f"待写入: {len(plan)} 条（group {TARGET_GROUP} 数学一）")
    from collections import Counter
    print("  按题型:", dict(Counter(p["qtype"] for p in plan)))
    print("  按年  :", dict(sorted(Counter(p["year"] for p in plan).items(), reverse=True)))
    n_sol = sum(1 for p in plan if "解答题结论" in (p["note"] or ""))
    print(f"  其中解答题结论 {n_sol} 条（自解析抽取）")
    letters = [p for p in plan if p["letter"]]
    print(f"  其中可机器判分(纯字母) {len(letters)} 条；填空/解答 {len(plan)-len(letters)} 条")

    if dry:
        print("\n[--dry] 未写库。样例：")
        for p in plan[:15]:
            print(f"   qid={p['id']} {p['year']}#{p['num']:<3} {p['qtype']:<4} "
                  f"-> {p['answer'][:50]:<52} [{p['note']}]")
        return

    added = ensure_columns(c)
    if added:
        print("  新增列:", added)

    for p in plan:
        c.execute("""UPDATE question
                        SET answer=?, answer_src=?, answer_note=?,
                            answer_letters=CASE WHEN ?<>'' THEN ? ELSE answer_letters END,
                            gradeable=CASE WHEN ?<>'' THEN 1 ELSE 0 END
                      WHERE id=?""",
                  (p["answer"], p["src"], p["note"],
                   p["letter"] or "", p["letter"] or "",
                   p["letter"] or "", p["id"]))
    c.commit()

    tot = c.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    gr = c.execute("SELECT COUNT(*) FROM question WHERE group_id=?", (TARGET_GROUP,)).fetchone()[0]
    have = c.execute("SELECT COUNT(*) FROM question WHERE group_id=? AND answer<>''",
                     (TARGET_GROUP,)).fetchone()[0]
    grade = c.execute("SELECT COUNT(*) FROM question WHERE gradeable=1").fetchone()[0]
    local = c.execute("SELECT COUNT(*) FROM question WHERE answer_src='local'").fetchone()[0]
    print(f"\n写入完成：题目 {tot}，group {TARGET_GROUP} 有答案 {have}/{gr}，"
          f"全库可判分 {grade}，本地回填 {local} 条")


if __name__ == "__main__":
    main()
