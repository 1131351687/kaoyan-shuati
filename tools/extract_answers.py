# -*- coding: utf-8 -*-
"""解析抽取器 v2 —— 支持全部四种格式，并保留完整答案文本。

格式清单（实测自 38 份解析文件 + 2025 真题）：
  A) 1987–2021 主格式：`(1)【答案】 (C).` / `（9）【答案】 $\\frac{1}{6}$ .`
     → 题号在【答案】之前，答案可为字母（选择题）或 LaTeX（填空题）
  A2) 同格式但题号写成 `1.` / `1、`
  B) 2023 格式：题干 `(1) 曲线...` → 选项 → 独行 `【答案】(B)`  ← 【答案】在题干之后
  C) 2022 格式：`1 设 ...` → 选项 `(A)...` → 独行 `答案 B.` → `分析` → `解`
  D) 2024 格式：`【1】<题干> … 【答案】（C）`
  E) 2025 真题：`【答案】`/`【解析】` 自带（在 真题/原始卷/2025年真题.md）
"""
import os
import re
import sys
import json
import sqlite3
from paths import ANSWERS_RAW, BANK_DB, TOOLS

SOL_DIR = STUDY_JIEXI
ZHENTI_DIR = STUDY_ZHENTI
BANK = BANK_DB
OUT = ANSWERS_RAW


def read(fp):
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def norm(s):
    s = s.strip()
    for a, b in (("（", "("), ("）", ")"), ("Ａ", "A"), ("Ｂ", "B"),
                 ("Ｃ", "C"), ("Ｄ", "D"), ("：", ":"),
                 # PDF 转文本常见私用区字形：\uf02d 是减号，\uf0b4 是 ×
                 ("\uf02d", "-"), ("\uf02b", "+"), ("\uf0b4", "*"),
                 ("−", "-"), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    return s


def letter_of(s):
    """仅当答案确为单选字母时返回 A/B/C/D，否则 None"""
    s = norm(s).rstrip(".．。 ")
    m = re.fullmatch(r"\(?\s*([ABCD])\s*\)?", s)
    return m.group(1) if m else None


def clean_ans(s):
    """清理答案文本：去掉包裹的 $ 与尾部句点"""
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    s = s.rstrip(" .．。")
    return s


def parse_sections(text):
    """切成 {题号: 答案文本}, 覆盖格式 A/A2/B/C/D

    题号合法性约束：只接受 1..25，并且拒绝 0/00（那是分节标题如
    「（1）～（8）小题」被误配）。

    ── 老卷（1987–2003）的关键事实 ──────────────────────────────────
    这些年份的解析文件**每个小节都从 (1) 重新编号**：
        # 一、填空题
        (1)【答案】 $x - y + z = 0$        ← 填空 #1
        ...
        # 五、选择题
        (1)【答案】 (C).                   ← 选择 #1（和新填 #1 撞号！）
        ...
    所以不能只按数字键 flat 存答案（setdefault 会丢掉后面的节）。
    这里改为按**题型分桶**：`{kind: {num: answer}}`，kind ∈
    {blank, choice, solve}，kind 由题目**所在小节的标题**决定。
    """
    got = {}
    LO, HI = 1, 25

    def ok(n):
        return LO <= n <= HI

    # ---- 先标注每个位置的"当前题型" ---------------------------------
    # 节标题形如 `# 一、填空题` / `# 五、选择题` / `# 三、`（老卷解答题）
    sec_marks = []      # [(pos, kind)]
    for m in re.finditer(r"(?m)^#{0,3}\s*[一二三四五六七八九十]+\s*[、.]\s*([^\n]*)", text):
        title = m.group(1)
        if "填空" in title:
            k = "blank"
        elif "选择" in title:
            k = "choice"
        elif "解答" in title or "计算" in title or "证明" in title:
            k = "solve"
        else:
            k = "solve"      # 老卷无关键词的节 = 解答题
        sec_marks.append((m.start(), k))

    def kind_at(pos):
        k = None
        for p, kk in sec_marks:
            if p <= pos:
                k = kk
            else:
                break
        return k or "blank"

    def put(pos, n, val):
        """按题型分桶存；同桶同号只留首次。

        修正：**答案本身是纯字母 (A)-(D) 的，一定是选择题**，不管它落在
        哪个小节标题下 —— 实测 2020 年解析把「选择题」错标成了
        `# 二、填空题`，导致 #1–8 的 (D)(C)(A)… 全被塞进 blank 桶，
        既污染填空答案、又让选择题答案丢失。以内容为准覆盖小节标签。
        """
        k = kind_at(pos)
        if letter_of(val):
            k = "choice"
        got.setdefault(k, {}).setdefault(n, val)

    # ---- D) 2024 风格： 【n】 … 【答案】（X）
    blocks = re.split(r"【(\d{1,2})】", text)
    if len(blocks) > 3:
        # 需要位置：用 finditer 重扫
        for m in re.finditer(r"【(\d{1,2})】([\s\S]*?)(?=【\d{1,2}】|$)", text):
            n = int(m.group(1))
            ma = re.search(r"【答案】\s*([^\n【]{0,80})", m.group(2))
            if ma and ok(n):
                put(m.start(), n, clean_ans(ma.group(1)))

    # ---- A/A2) 题号在【答案】之前（要求题号紧邻【答案】，中间只允许空白）
    for m in re.finditer(r"[（(]\s*(\d{1,2})\s*[）)]\s*【答案】\s*([^\n]{0,100})", text):
        n = int(m.group(1))
        if ok(n):
            put(m.start(), n, clean_ans(m.group(2)))
    for m in re.finditer(r"(?:^|\n)\s*(\d{1,2})\s*[.、]\s*【答案】\s*([^\n]{0,100})", text):
        n = int(m.group(1))
        if ok(n):
            put(m.start(), n, clean_ans(m.group(2)))

    # ---- B) 2023 风格：【答案】独立成行，题号向上回找最近的「行首」(n)
    qmarks = [(m.start(), int(m.group(1)))
              for m in re.finditer(r"(?:^|\n)\s*[（(]\s*(\d{1,2})\s*[）)]\s*\S", text)]
    for m in re.finditer(r"【答案】\s*([^\n【]{0,80})", text):
        prev = [x for x in qmarks if x[0] < m.start()]
        if prev:
            n = prev[-1][1]
            if ok(n):
                put(prev[-1][0], n, clean_ans(m.group(1)))

    # ---- C) 2022 风格：独行 `答案 X.`，题号向上回找行首裸数字
    lines = text.split("\n")
    qline = {}
    off = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*(\d{1,2})\s+(?![:：.、])\S", ln)
        if m:
            num = int(m.group(1))
            if ok(num):
                qline[i] = num
        off += len(ln) + 1
    pos = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*答案\s*([ABCD]\b[^\n]{0,40})", ln)
        if m:
            prev = [k for k in qline if k < i]
            if prev:
                n = qline[max(prev)]
                put(pos, n, clean_ans(m.group(1)))
        pos += len(ln) + 1

    return got


def parse_quickref(text):
    """解析文末「答案速查」区：`(n) X.` 每题一行。

    2022 年解析的文末有 `# 2022年...答案速查` 区块，格式最干净：
        # 一、选择题
        (1)B.
        ...
        # 二、填空题
        (11)4.
        ...
    这是最可靠的来源，优先级应高于正文里的 `答案 X.`（正文常夹
    【例】…（20xx年数学二试题）的串题，会污染题号回找）。
    """
    got = {}
    idx = text.find("答案速查")
    if idx < 0:
        return got
    for m in re.finditer(r"[（(]\s*(\d{1,2})\s*[）)]\s*([^\n]{1,120})", text[idx:]):
        n = int(m.group(1))
        val = clean_ans(norm(m.group(2)))
        if 1 <= n <= 25 and val:
            got.setdefault(n, val)
    return got


def parse_quickref_typed(text):
    """「答案速查」区按题型分桶版：区里自带 `# 一、选择题` / `# 二、填空题` 小节。

    返回 {kind: {num: ans}}；找不到速查区返回 {}。
    """
    idx = text.find("答案速查")
    if idx < 0:
        return {}
    tail = text[idx:]
    sec_marks = []
    for m in re.finditer(r"(?m)^#{0,3}\s*[一二三四五六七八九十]+\s*[、.]\s*([^\n]*)", tail):
        t = m.group(1)
        k = ("blank" if "填空" in t else
             "choice" if "选择" in t else
             "solve")
        sec_marks.append((m.start(), k))

    def kind_at(pos):
        k = None
        for p, kk in sec_marks:
            if p <= pos:
                k = kk
            else:
                break
        return k or "choice"

    out = {}
    for m in re.finditer(r"[（(]\s*(\d{1,2})\s*[）)]\s*([^\n]{1,120})", tail):
        n = int(m.group(1))
        val = clean_ans(norm(m.group(2)))
        if 1 <= n <= 25 and val:
            out.setdefault(kind_at(m.start()), {}).setdefault(n, val)
    return out


def parse_zhenti_2025():
    """2025 真题.md 自带答案：题干行形如 `1. xxx`，答案紧跟在 `【答案】` 行。

    实测该文件的题号**并非全部**是 `n.` 列表标记（1–11 题用的是别的形式），
    所以不能只靠 `(?:^|\\n)\\s*(\\d{1,2})\\s*[.、]` 定位题号 —— 那会把 #11 的答案
    错配到 #12。改为：按【答案】出现顺序 + 题号出现顺序做**顺序配对**，
    并用 `【解析】` 之前的最后一段文本来确定题号。
    """
    fp = os.path.join(ZHENTI_DIR, "2025年真题.md")
    if not os.path.exists(fp):
        return {}
    text = read(fp)
    got = {}

    # 题干行形如 `1．`（注意是全角句点 U+FF0E）/ `1.` / `1、` / `第1题`
    marks = [(m.start(), int(m.group(1)))
             for m in re.finditer(
                 r"(?:^|\n)\s*(?:第\s*)?(\d{1,2})\s*[.．、。]\s*(?!\d)", text)]

    for m in re.finditer(r"【答案】\s*([^\n【]*)", text):
        val = norm(m.group(1)).strip()
        prev = [x for x in marks if x[0] < m.start()]
        if not prev:
            continue
        n = prev[-1][1]
        if 1 <= n <= 25 and val:
            got.setdefault(n, clean_ans(val))
    return got


def main():
    years = sorted(int(m.group(1)) for m in
                   (re.match(r"(\d{4})年解析\.md$", f) for f in os.listdir(SOL_DIR))
                   if m)
    print(f"解析文件: {len(years)} 个 ({years[0]}–{years[-1]})")

    all_ans = {}
    for y in years:
        fp = os.path.join(SOL_DIR, f"{y}年解析.md")
        if not os.path.exists(fp):
            continue
        text = read(fp)
        got = parse_sections(text)          # {kind: {num: ans}}
        # 「答案速查」区比正文更可靠，用它逐桶覆盖正文结果
        for k, m in parse_quickref_typed(text).items():
            got.setdefault(k, {}).update(m)
        # 兼容旧版平的速查解析（无小节标题时归到 choice）
        flat = parse_quickref(text)
        if flat:
            got.setdefault("choice", {})
            for n, v in flat.items():
                got["choice"].setdefault(n, v)
        all_ans[y] = got
        cnt = {k: len(v) for k, v in got.items()}
        print(f"  {y}: {cnt}")

    all_ans[2025] = {"choice": parse_zhenti_2025()}
    print(f"  2025(真题): {len(all_ans[2025]['choice'])} 个  题号={sorted(all_ans[2025]['choice'])}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(all_ans, f, ensure_ascii=False, indent=1)
    print(f"\n已写 {OUT}")

    # ---------- 与题库比对 ----------
    # 本地资料只覆盖「数学一」→ 仅 group 8 可回填；
    # group 9(数学二)/10(数学三) 虽同样有 2009–2024 年份，但试卷内容不同，
    # 绝不能用数学一的答案去填 —— 之前按年份直接匹配造成的假阳性。
    c = sqlite3.connect(BANK)
    c.row_factory = sqlite3.Row
    q_by = {}
    for r in c.execute("SELECT id, group_id, year, number, qtype_name, gradeable FROM question"):
        q_by.setdefault(r["group_id"], []).append(r)

    TARGET = "8"
    print(f"\n=== 覆盖率（仅 group {TARGET} 数学一有本地资料）===")
    for g in sorted(q_by):
        gr = q_by[g]
        need = [r for r in gr if not r["gradeable"]]
        fill = []
        if g == TARGET:
            for r in need:
                y = int(r["year"]) if str(r["year"]).isdigit() else 0
                n = int(r["number"]) if str(r["number"]).isdigit() else None
                if not n or y not in all_ans:
                    continue
                kind = {"选择题": "choice", "填空题": "blank",
                        "解答题": "solve"}.get(r["qtype_name"], "choice")
                v = all_ans[y].get(kind, {}).get(n)
                if v:
                    fill.append((r["id"], y, n, r["qtype_name"], v))
            mark = ""
        else:
            mark = "  ← 无本地资料，不回填"
        nm = {"8": "数学一", "9": "数学二", "10": "数学三"}.get(g, g)
        print(f"  group {g} ({nm}): 总{len(gr)} 待补{len(need)}  **可回填 {len(fill)}**{mark}")
        if fill:
            from collections import Counter
            print("     按年:", dict(sorted(Counter(x[1] for x in fill).items(), reverse=True)))
            print("     按题型:", dict(Counter(x[3] for x in fill)))
            for _, y, n, t, a in sorted(fill, key=lambda x: (-x[1], x[2]))[:12]:
                print(f"       {y} #{n:>2} {t}  ->  {a[:56]}")
    print("\n注: 本地资料只有数学一，故仅 group 8 可回填。")
    return q_by, TARGET


if __name__ == "__main__":
    main()
