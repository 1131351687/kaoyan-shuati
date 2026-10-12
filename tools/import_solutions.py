# -*- coding: utf-8 -*-
r"""把 LLM 判定为「真答案」的解答题结论写回 question.answer。

前置：
  1. extract_solutions.py   → tools/_build/_solutions_raw.json（规则抽取候选）
  2. llm_solution_filter.py --write → question.sol_ok / sol_conf / sol_why

本脚本做第 3 步：把 sol_ok=1 的候选写进 answer，并标注来源。
answer_src 取值：
    site   站点自带（选择题/部分填空题）
    local  本地真题解析回填（extract_answers.py）
    sol    本地解析的解答题结论（本脚本，经 LLM 判定）

另外为所有经 LLM 审视过的解答题写 answer_flag（质量标注，不删答案）：
    ''      未经 LLM 审视
    'ok'    LLM 认可为最终答案
    'doubt' LLM 判为中间推导/方法描述/残片 —— 答案仍保留，但界面会提示核对

为什么「降级」而不是「删除」：实测 LLM 有真错判，例如
2024#20 的 `\frac{4\pi}{5\sqrt{5}}` 明明是曲面积分的最终值，
却被判为"曲线积分表达式，非最终数值结果"。宁可留个提示，不可丢掉正确答案。
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import SOLUTIONS_RAW, BANK_DB


def clean_for_answer(s):
    """答案入库前的清理：去 HTML、压空白、修常见 OCR 空格注入。"""
    s = re.sub(r"<[^>]+>", "", s)                       # HTML 表格/标签
    s = re.sub(r"\s+", " ", s).strip()
    # LaTeX 内的空格注入：`\f r a c` → `\frac`
    s = re.sub(r"\\\s+([a-zA-Z]+)", lambda m: "\\" + m.group(1), s)
    s = re.sub(r"\{\s+", "{", s)
    s = re.sub(r"\s+\}", "}", s)
    return s.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--min-conf", type=float, default=0.7, help="最低置信度")
    args = ap.parse_args()

    raw = json.load(open(SOLUTIONS_RAW, encoding="utf-8"))
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row

    # 只处理解答题、且当前无答案、且 LLM 判为真答案的
    todo = []
    for year, items in raw.items():
        for num, v in items.items():
            got = c.execute(
                "SELECT id, answer, sol_ok, sol_conf, sol_why FROM question "
                "WHERE group_id='8' AND year=? AND number=? AND qtype_name='解答题'",
                (year, num)).fetchone()
            if not got:
                continue
            if (got["answer"] or "").strip():
                continue                       # 已有答案，不动
            if got["sol_ok"] != 1:
                continue
            if (got["sol_conf"] or 0) < args.min_conf:
                continue
            todo.append((got["id"], year, num, v["ans"], got["sol_conf"], got["sol_why"]))

    print(f"待写回 {len(todo)} 条（解答题、无答案、LLM 判为真、conf≥{args.min_conf}）")
    for qid, year, num, ans, conf, why in todo[:8]:
        print(f"  {year}#{num}  id={qid}  conf={conf:.2f}  {clean_for_answer(ans)[:80]}")

    if not args.write:
        print("\n（未加 --write，未写库）")
        return 0

    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    if "answer_note" not in cols:
        c.execute("ALTER TABLE question ADD COLUMN answer_note TEXT DEFAULT ''")
    if "answer_flag" not in cols:
        c.execute("ALTER TABLE question ADD COLUMN answer_flag TEXT DEFAULT ''")

    # ── 步骤 1: 把判为真答案、且当前无答案的写进 answer ──
    n = 0
    for qid, year, num, ans, conf, why in todo:
        clean = clean_for_answer(ans)
        if not clean:
            continue
        c.execute("UPDATE question SET answer=?, answer_src='sol', answer_note=? WHERE id=?",
                  (clean, f"本地解析结论（LLM 判定 conf={conf:.2f}）：{why or ''}", qid))
        n += 1
    c.commit()
    print(f"\n已写回 {n} 条（answer_src='sol'）")

    # ── 步骤 2: 给所有被 LLM 审视过的解答题打质量标记 ──
    #    只对「当前已有答案」的处理；无答案的不标记（没东西可标）
    flagged = {"ok": 0, "doubt": 0}
    for year, items in raw.items():
        for num in items:
            got = c.execute(
                "SELECT id, answer, sol_ok FROM question "
                "WHERE group_id='8' AND year=? AND number=? AND qtype_name='解答题'",
                (year, num)).fetchone()
            if not got or got["sol_ok"] is None:
                continue
            if not (got["answer"] or "").strip():
                continue
            flag = "ok" if got["sol_ok"] == 1 else "doubt"
            c.execute("UPDATE question SET answer_flag=? WHERE id=?", (flag, got["id"]))
            flagged[flag] += 1
    c.commit()
    print(f"已标注 answer_flag: ok={flagged['ok']}  doubt={flagged['doubt']}"
          f"（doubt 表示 LLM 认为该答案可能是中间推导，界面会提示核对）")

    # 统计
    print("\n--- 写后统计（group 8）---")
    for r in c.execute("""SELECT qtype_name, COUNT(*) n,
                                 SUM(CASE WHEN TRIM(COALESCE(answer,''))<>'' THEN 1 ELSE 0 END) a,
                                 SUM(CASE WHEN answer_flag='doubt' THEN 1 ELSE 0 END) d
                          FROM question WHERE group_id='8' GROUP BY qtype_name"""):
        print(f"  {r['qtype_name']}: {r['n']} 题 / {r['a']} 有答案 / {r['d']} 存疑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
