# -*- coding: utf-8 -*-
"""把「李艳芳预测三套卷」导入本地题库，作为独立的模拟卷栏目。

数据源（唯一机器可读）：
  D:\\学习\\考研\\数学一\\模拟卷\\李艳芳预测三套卷\\可检索文本\\{2024,2025,2026} ... 试题.md

重要事实：**这三个 md 只有题干，没有答案**；配套解析 PDF 为扫描图
（`/Font: 0`，无文字层），2026 只有「答案速查」PDF 同样无文字层。
因此导入的模拟卷 answer 一律为空、gradeable=0，UI 明确标注「本站未收录答案」。

写入 ztq_bank.db：
  - 复用 question 表，group_id 用 'mock2024' / 'mock2025' / 'mock2026'
    （字符串组号，天然与站点 group 8/9/10 数字组隔离，不会被答案回填误伤）
  - 新增 paper_set 列记录「第几套」
  - 同时产出 D:\\try\\bank\\mock\\<year>_<set>.md 原始试题存档
"""
import os
import re
import json
import sqlite3
from paths import BANK_DB, MOCK, STUDY_MOCK, TOOLS

SRC = STUDY_MOCK
BANK = BANK_DB
MOCK_DIR = MOCK

FILES = {
    2024: "2024 李艳芳 预测三套卷 试题.md",
    2025: "2025 李艳芳 3套卷 试题.md",
    2026: "2026 李艳芳 三套卷 试题.md",
}


def read(fp):
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def split_sets(text):
    """按 `# 一级标题` 切成三套（跳过 2026 的「目录」）"""
    parts = re.split(r"(?m)^#\s+(.+)$", text)
    # parts = [pre, title1, body1, title2, body2, ...]
    out = []
    for i in range(1, len(parts) - 1, 2):
        title, body = parts[i].strip(), parts[i + 1]
        if title in ("目录",) or body.strip() == "":
            continue
        out.append((title, body))
    return out


def parse_questions(body):
    """抽题：按 (n) / （n） 标记切块（含 `## (17) (本题满分 10 分)` 这类标题），
    并归属到 选择/填空/解答 三节。

    返回 [(num, qtype, qtype_name, score, stem)]
    """
    lines = body.split("\n")
    cur_section = None            # 'choice' | 'blank' | 'solve'

    section_re = re.compile(r"^#{0,3}\s*([一二三])、\s*(选择题|填空题|解答题)")
    # 题号行：允许前面有 ## / ### 标题标记
    qstart_re = re.compile(r"^#{0,3}\s*[（(]\s*(\d{1,2})\s*[）)]\s*(?:[（(]本题满分\s*(\d+)\s*分[）)])?")
    # 非题号行但可能内联紧跟上一题（如 `(2) 设 0<a<1, ...`）

    q = []
    cur = None
    for ln in lines:
        ms = section_re.match(ln)
        if ms:
            cur_section = {"选择题": "choice", "填空题": "blank",
                           "解答题": "solve"}[ms.group(2)]
            continue

        mq = qstart_re.match(ln)
        if mq:
            num = int(mq.group(1))
            # 只接受本题号属于当前节的合理范围，避免把选项行 (A)(B) 当题号
            ok = True
            if ln.lstrip().startswith("#"):
                ok = True
            else:
                # 非标题行：必须是行首题号（无缩进），排除选项行
                ok = not re.match(r"^\s*[（(]\s*[ABCD]\s*[）)]", ln)
            if ok:
                if cur is not None:
                    q.append(cur)
                cur = [num, cur_section, [ln.strip()]]
                continue

        if cur is not None:
            cur[2].append(ln.rstrip())

    if cur is not None:
        q.append(cur)

    CN = {"choice": ("选择题", 5), "blank": ("填空题", 5), "solve": ("解答题", 12)}
    res = []
    for num, sec, sl in q:
        sec = sec or "choice"
        name, score = CN[sec]
        # 解答题 17 题固定 10 分；其余解答题 12 分
        if sec == "solve":
            head = sl[0] if sl else ""
            m = re.search(r"本题满分\s*(\d+)\s*分", head)
            score = int(m.group(1)) if m else (10 if num == 17 else 12)
        stem = "\n".join(sl).strip()
        if len(stem) < 12:
            continue
        res.append((num, sec, name, score, stem))
    return res


def main():
    os.makedirs(MOCK_DIR, exist_ok=True)
    c = sqlite3.connect(BANK)
    c.row_factory = sqlite3.Row

    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    if "paper_set" not in cols:
        c.execute("ALTER TABLE question ADD COLUMN paper_set TEXT")
        print("新增列 paper_set")
    if "stem" not in cols:
        c.execute("ALTER TABLE question ADD COLUMN stem TEXT DEFAULT ''")
        print("新增列 stem（题干原文；站点真题为空，题图才是其载体）")

    # 清掉旧的模拟卷行，保证幂等
    c.execute("DELETE FROM question WHERE group_id LIKE 'mock%'")

    total = 0
    summary = {}
    for year, fn in sorted(FILES.items()):
        fp = os.path.join(SRC, fn)
        text = read(fp)
        sets = split_sets(text)
        print(f"\n{year}: 切成 {len(sets)} 套")
        gid = f"mock{year}"
        for si, (title, body) in enumerate(sets, 1):
            qs = parse_questions(body)
            from collections import Counter
            dist = Counter(x[2] for x in qs)
            print(f"  第{si}套「{title[:40]}」: {len(qs)} 题  {dict(dist)}")
            # 存档原始 md
            ap = os.path.join(MOCK_DIR, f"{year}_set{si}.md")
            with open(ap, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n{body.strip()}\n")
            for num, sec, name, score, stem in qs:
                c.execute("""INSERT INTO question
                    (id, group_id, group_name, year, paper_name, paper_id, number,
                     qtype, qtype_name, score, answer, gradeable, answer_letters,
                     kp_name, kp_id, kp_lv1, kp_lv2, img_local, img_url,
                     diff_n, happy, maybe, sad, mastery, easy_ratio,
                     answer_src, answer_note, paper_set, stem)
                    VALUES (?,?,?,?,?,?,?, ?,?,?,?,?,?, ?,?,?,?,?,?, 0,0,0,0,NULL,NULL, ?,?,?,?)""",
                    (f"{gid}-{si}-{num}", gid, f"李艳芳预测三套卷(数学一)",
                     str(year), f"{year} 第{si}套", f"{gid}-{si}", str(num),
                     {"choice": 1, "blank": 2, "solve": 3}[sec], name, score,
                     "", 0, "", "", "", "", "", "", "",
                     "mock", "本地模拟卷(无答案)", f"第{si}套", stem))
                total += 1
            summary.setdefault(year, []).append(len(qs))

    c.commit()
    print(f"\n合计导入模拟卷 {total} 题")
    print("  按年:", {k: v for k, v in sorted(summary.items())})

    # 写一份给前端用的模拟卷清单
    lst = []
    for year in sorted(FILES):
        for si, n in enumerate(summary.get(year, []), 1):
            row = c.execute("""SELECT COUNT(*) n, SUM(score) s FROM question
                               WHERE group_id=? AND paper_set=?""",
                            (f"mock{year}", f"第{si}套")).fetchone()
            lst.append({"group_id": f"mock{year}", "year": year, "set": si,
                        "name": f"{year} 李艳芳预测三套卷 第{si}套",
                        "count": row["n"], "total_score": row["s"] or 0,
                        "answer_pdf": "扫描版PDF，无文字层"})
    mp = r"MOCK\index.json"
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"note": "模拟卷题干已本地化；答案在扫描版 PDF 中，未数字化",
                   "papers": lst}, f, ensure_ascii=False, indent=1)
    print(f"清单已写 {mp}")

    tot = c.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    mock = c.execute("SELECT COUNT(*) FROM question WHERE group_id LIKE 'mock%'").fetchone()[0]
    print(f"\n题库现状: 总计 {tot} 题（其中模拟卷 {mock} 题）")


if __name__ == "__main__":
    main()
