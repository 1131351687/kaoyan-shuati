# -*- coding: utf-8 -*-
"""把 1987–2008 年真题（站点完全没有收录）导入本地题库。

数据源：
  D:\\学习\\考研\\数学一\\真题\\原始卷\\<year>年真题.md   （题干，数学一）
  D:\\学习\\考研\\数学一\\解析\\<year>年解析.md          （答案/解析，由
                                                        extract_answers.py 预抽成
                                                        _answers_raw.json）

要点：
  - group_id 用 'old<year>'，与站点 group 8/9/10 及 mock* 隔离
  - 这些老卷的解析是"逐题散文"风格（2000+ 才有 `三、解答题` 分节），
    因此题干用宽松切分：按 `一、/二、/…/十一、` 中文数字分节，
    节标题里含"填空/选择/解答"的判题型，否则按节内题号范围推断。
  - 答案只从 _answers_raw.json 取（一次性预抽好），不在本脚本里重跑正则。
"""
import os
import re
import json
import sqlite3
from paths import ANSWERS_RAW, BANK_DB, SOLUTIONS_RAW, STUDY_ZHENTI, TOOLS

SRC = STUDY_ZHENTI
BANK = BANK_DB
RAW = ANSWERS_RAW
SOL = SOLUTIONS_RAW

YEAR_MIN, YEAR_MAX = 1987, 2008

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
      "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14,
      "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20}

# 节标题： `# 一、填空题（本题共5小题…）`
SEC_RE = re.compile(r"^#{0,3}\s*([一二三四五六七八九十]+)\s*[、.]\s*(.*)$")
# 题号行： `(1) …` / `（1）…` / `(1)（本题满分3分）…`
Q_RE = re.compile(r"^#{0,3}\s*[（(]\s*(\d{1,2})\s*[）)]\s*")
# 选项行
OPT_RE = re.compile(r"^#{0,3}\s*[（(]\s*([ABCD])\s*[）)]")


def read(fp):
    with open(fp, encoding="utf-8", errors="replace") as f:
        return f.read()


def guess_type(sec_title, nums, seen_kinds=()):
    """
    判题型。

    老卷的节标题形如 `# 三、（本题共2小题，每小题5分，满分10分）` —— **不带题型关键词**，
    所以光看标题不够。实测规律（1987–2003 全部符合）：
      - 有「填空题」「选择题」字样的节直接定；
      - 其余无关键词的节一律是**解答题**（老卷解答题按每题分值单独分节，
        所以会有一堆 `（本题满分N分）` 的节，每节 1–3 小题）；
      - 只有极少数年份把填空题/选择题放在**卷末**（那时标题也会写出来，仍可识别）。
    """
    t = sec_title or ""
    if "填空" in t:
        return "blank"
    if "选择" in t:
        return "choice"
    if "解答" in t or "计算" in t or "证明" in t:
        return "solve"
    # 无关键词：老卷里就是解答题（选择题/填空题一定会写明）
    if nums:
        return "solve"
    return "solve"


def parse_paper(text):
    """
    返回 [(num, sec_kind, qtype_name, score, stem)]
    sec_kind ∈ {choice, blank, solve}
    """
    # 丢掉文首的卷头（`# 1987年全国…` / `# 数学（一）` / `（科目代码：301）`）
    lines = text.split("\n")

    # 切节
    secs = []          # [(sec_idx, title, [lines])]
    cur = (-1, "", [])
    for ln in lines:
        m = SEC_RE.match(ln)
        if m and m.group(1) in CN and not ln.lstrip().startswith("（科目"):
            secs.append(cur)
            cur = (CN[m.group(1)], m.group(2), [])
            continue
        cur[2].append(ln)
    secs.append(cur)
    secs = [s for s in secs if s[2]]

    out = []
    for idx, title, body in secs:
        # 该节里所有题号
        nums = [int(m.group(1)) for m in
                (Q_RE.match(l) for l in body) if m]
        if not nums:
            continue
        kind = guess_type(title, nums)
        # 每小节按 ±3 分给分
        for ln in body:
            mq = Q_RE.match(ln)
            if not mq:
                continue
            num = int(mq.group(1))
            # 选项行 (A)(B) 也会命中 Q_RE 吗？不会（A-D 非数字）
            # 收集本题正文：从该行到下个题号/选项块结束
            out.append((num, kind, ln.strip()))

    # 二次归集：把同一题号的后续行并进去
    res = []
    i = 0
    body_lines = [l for _, _, l in out]
    # 用原始 body 顺序重扫更稳
    res = _collect(secs)
    return res


def _collect(secs):
    """
    按节顺序收集题干。

    关键：**老卷的题号在每个小节内从 (1) 重新开始**（例如 1996 年有
    `一、填空题` 的 (1)-(5)、`二、选择题` 的 (1)-(5)、`三、` 的 (1)-(2) …），
    所以唯一键必须是 (节号, 题号)，不能用题号单独去重。
    返回 [(sec_idx, num, kind, lines)]
    """
    res = []
    for idx, title, body in secs:
        nums = [int(m.group(1)) for m in
                (Q_RE.match(l) for l in body) if m]
        if not nums:
            continue
        kind = guess_type(title, nums)
        cur_num, cur_lines = None, []
        for ln in body:
            mq = Q_RE.match(ln)
            if mq and not OPT_RE.match(ln):
                if cur_num is not None:
                    res.append((idx, cur_num, kind, cur_lines))
                cur_num = int(mq.group(1))
                cur_lines = [ln.strip()]
            elif cur_num is not None:
                cur_lines.append(ln.rstrip())
        if cur_num is not None:
            res.append((idx, cur_num, kind, cur_lines))
    return res


SCORE = {"choice": 5, "blank": 4, "solve": 10}


def main():
    all_ans = {}
    for k, v in json.load(open(RAW, encoding="utf-8")).items():
        y = int(k)
        all_ans[y] = {kind: {str(a): b for a, b in bucket.items()}
                      for kind, bucket in v.items()}
    sols = {}
    if os.path.exists(SOL):
        sr = json.load(open(SOL, encoding="utf-8"))
        for y, items in sr.items():
            for n, v in items.items():
                if v.get("conf") == "high" and (v.get("ans") or "").strip():
                    sols.setdefault(int(y), {})[int(n)] = v["ans"].strip()

    c = sqlite3.connect(BANK)
    c.row_factory = sqlite3.Row
    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    for col, ddl in (("paper_set", "TEXT"), ("stem", "TEXT DEFAULT ''"),
                     ("answer_src", "TEXT DEFAULT 'site'"),
                     ("answer_note", "TEXT DEFAULT ''")):
        if col not in cols:
            c.execute(f"ALTER TABLE question ADD COLUMN {col} {ddl}")
            print("新增列", col)

    c.execute("DELETE FROM question WHERE group_id LIKE 'old%'")

    total = 0
    summary = {}
    for year in range(YEAR_MIN, YEAR_MAX + 1):
        fp = os.path.join(SRC, f"{year}年真题.md")
        if not os.path.exists(fp):
            print(f"{year}: 缺真题文件，跳过")
            continue
        text = read(fp)
        raw_items = _collect(_sections(text))
        if not raw_items:
            print(f"{year}: 未解析出题目")
            continue

        from collections import Counter
        dist = Counter(k for _, _, k, _ in raw_items)
        # 去重键 = (节号, 题号)；重号只保留首见
        seen, uniq = set(), []
        for sidx, num, kind, ls in raw_items:
            key = (sidx, num)
            if key in seen:
                continue
            seen.add(key)
            uniq.append((sidx, num, kind, ls))

        # 全局连续编号：老卷题号会分段重来，展示时用节序*100+题号太丑，
        # 改用 1..N 顺序号，同时保留原始「节-题」信息在 stem 里。
        gid = f"old{year}"

        # ── 解答题序号映射 ────────────────────────────────────────────
        # 老卷每个解答小节都从 (1) 编号；解析文件里的解答题则是全卷连续编号。
        # 因此把本卷解答题按出现顺序编号 1..M，即对应解析里的解答 #1..#M。
        solve_seq = {}
        si = 0
        for sidx, num, kind, _ls in uniq:
            if kind != "solve":
                continue
            si += 1
            solve_seq[(sidx, num)] = si

        # 第一个填空题小节（只有它对应解析里的 填空 #1..N）
        blank_first_sec = {}
        for sidx, num, kind, _ls in uniq:
            if kind == "blank" and kind not in blank_first_sec:
                blank_first_sec[kind] = sidx
            if kind == "choice" and "choice" not in blank_first_sec:
                blank_first_sec["choice"] = sidx

        n_add = 0
        for order, (sidx, num, kind, ls) in enumerate(uniq, start=1):
            stem = "\n".join(ls).strip()
            if len(stem) < 8:
                continue
            tname = {"choice": "选择题", "blank": "填空题", "solve": "解答题"}[kind]
            # ── 答案匹配（老卷必须按题型分别对号）──────────────────────────
            # 关键事实：`_answers_raw.json` 里的编号是解析文件里**填空题的 1..N**
            # （1987–2003 的解析只抽得到填空题答案，因为那些年选择题/解答题的
            # 答案写在正文里、版式各不相同）。所以：
            #   填空题 → 直接用 num 去 all_ans 查（num 就是填空内部的序号）
            #   选择题 → all_ans 里没有，只能靠 sols 里的解答题结论（也没有）
            #   解答题 → 用 sols，但 sols 的编号是**解答题内部序号**，
            #            而老卷每个解答小节都从 (1) 重新编号 → 必须把
            #            本卷解答题的「第几个」转成解析里的序号。
            ans, src, note = "", "", ""
            per_year = all_ans.get(year) or {}
            if kind == "blank":
                # 填空题可能分多个小节（老卷常在卷末再来一组），
                # 只有**第一个**填空题小节对应解析里的填空 #1..N，
                # 后续小节不硬套，避免张冠李戴。
                bfirst = blank_first_sec.get("blank")
                bucket = per_year.get("blank") or {}
                if bfirst is not None and sidx == bfirst and str(num) in bucket:
                    ans = bucket[str(num)].strip()
                    src, note = "local", f"{year}年解析.md 填空#{num}"
            elif kind == "choice":
                cfirst = blank_first_sec.get("choice")
                bucket = per_year.get("choice") or {}
                if cfirst is not None and sidx == cfirst and str(num) in bucket:
                    ans = bucket[str(num)].strip()
                    src, note = "local", f"{year}年解析.md 选择#{num}"
            elif kind == "solve" and year in sols:
                # 解答题按本卷第 order_solve 个去对
                sn = solve_seq.get((sidx, num))
                if sn is not None and sn in sols[year]:
                    ans = sols[year][sn]
                    src, note = "local", f"{year}年解析.md 解答#{sn}（结论）"
            letter = ""
            if ans:
                m = re.fullmatch(r"\(?\s*([ABCD])\s*\)?",
                                 ans.replace("（", "(").replace("）", ")"))
                letter = m.group(1) if m else ""
            c.execute("""INSERT INTO question
                (id, group_id, group_name, year, paper_name, paper_id, number,
                 qtype, qtype_name, score, answer, gradeable, answer_letters,
                 kp_name, kp_id, kp_lv1, kp_lv2, img_local, img_url,
                 diff_n, happy, maybe, sad, mastery, easy_ratio,
                 answer_src, answer_note, paper_set, stem)
                VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?, 0,0,0,0,NULL,NULL, ?,?,?,?)""",
                (f"{gid}-{order}", gid, f"{year}年真题（数学一）· 本地",
                 str(year), f"{year}年真题", gid, str(order),
                 {"choice": 1, "blank": 2, "solve": 3}[kind], tname,
                 SCORE[kind], ans, 1 if letter else 0, letter,
                 "", "", "", "", "", "",
                 src or "none", note, f"原卷#{num}", stem))
            n_add += 1
        c.commit()
        total += n_add
        summary[year] = (n_add, dict(dist))
        print(f"{year}: {n_add} 题 {dict(dist)}")

    print(f"\n合计导入 {total} 题（{YEAR_MIN}–{YEAR_MAX}）")
    tot = c.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    old = c.execute("SELECT COUNT(*) FROM question WHERE group_id LIKE 'old%'").fetchone()[0]
    oa = c.execute("SELECT COUNT(*) FROM question WHERE group_id LIKE 'old%' AND answer<>''").fetchone()[0]
    print(f"题库现状: 总计 {tot} 题，其中老卷 {old} 题（{oa} 有答案）")


def _sections(text):
    """把文本切成 [(sec_idx, title, [lines])]"""
    lines = text.split("\n")
    secs, cur = [], (-1, "", [])
    for ln in lines:
        m = SEC_RE.match(ln)
        if m and m.group(1) in CN and "科目代码" not in ln:
            secs.append(cur)
            cur = (CN[m.group(1)], m.group(2), [])
            continue
        cur[2].append(ln)
    secs.append(cur)
    return [s for s in secs if s[2]]


if __name__ == "__main__":
    main()
