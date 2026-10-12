r"""import_site_stems.py —— 给站点真题补文字题干（优先本地文本，OCR 兜底）

背景：站点 1224 道真题只有题图（img_local），没有文字题干（stem 全空），
导致：
  · 无法用 LLM 评估难度（LLM 只吃文字）
  · 网页上必须依赖图片，不能搜索/复制题干

策略（用户 m02250 指令）：
  ① 优先用本地真题文本  D:\学习\考研\数学一\真题\原始卷\<年>年真题.md
  ② 本地没有或质量不可用的年份 → OCR 兜底。

【重要】本地文本并非每年都能用（见 tools/probe_stem_sources.py 打分）：
  · 2009–2021  格式规整 `(n) 题干 (A)…(D)`，23/22 题齐全 → 直接可用
  · 2022       OCR 严重损坏（正文乱码）→ 必须 OCR
  · 2023       卷头「2. 密封线…」被误判为题号，整体错位 → 必须 OCR
  · 2024       题号错位（“…则下列结论正确的是 / 3阶矩阵A的秩为2”被切开）→ 必须 OCR
  · 2025       干净（裸 `1.` 题号，22 题完整）→ 可用，但内含解析需剥离
  · 2026       本地无 → 必须 OCR

因此用 TEXT_OK_YEARS 白名单控制，只从「已验证可靠」的年份取文本。

站点题号与本地卷题号一一对应（站点每卷 #1..#23 或 #1..#22）。

用法：
    python tools/import_site_stems.py                 # 预览
    python tools/import_site_stems.py --write         # 写库
    python tools/import_site_stems.py --ocr-report    # 列出需 OCR 的题
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB, STUDY_ZHENTI, DATA, ensure_dirs  # noqa: E402

# STUDY_ZHENTI 已经指向「真题\原始卷」，直接用
RAW = STUDY_ZHENTI

# 只有数学一（group 8）有本地文字资料
TEXT_GROUPS = {"8"}

# 本地文本「已验证可靠」的年份白名单（其余走 OCR）。
# 判据：parse_exam() 能解析出完整题数（≤2020 为 23 题，≥2021 为 22 题）。
# 实测：
#   2009–2020  完整 23 题  → 文案可用
#   2021       填空题题号被源文件损毁（11) (2) (3) 4） 5） (16)）→ OCR
#   2022       正文 OCR 乱码 → OCR
#   2023       卷头「2. 密封线…」被误判题号 → OCR
#   2024       题号错位（“…则下列结论正确的是 3阶矩阵A…”被切开）→ OCR
#   2025       仅解析出 20/22 题（缺 19、22）→ OCR
#   2026       本地无该年文件 → OCR
TEXT_OK_YEARS = {2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018,
                 2019, 2020}


def _strip_inline_solution(body):
    """去掉内联的【答案】【解析】段落（2025 卷把解析也写进正文）。"""
    body = re.split(r"【答案】|【解析】|【详解】", body)[0]
    return body


def _find_starts(text):
    """找题目起点，返回 [(start, end, number)]。

    本地各卷题号格式并不统一，实测见过：
        (1) xxx     ← 2009–2019 主流
        9) xxx      ← 2020 填空题（缺左括号）
        11) xxx     ← 2021
        4）xxx      ← 2021（全角右括号）
        11．xxx     ← 2025（点号）
        1. xxx      ← 2023
    所以按「行首 + 数字 + 可选开括号 + 必选闭括号/点号」宽松匹配，
    再用「题号递增」约束剔除误匹配。
    """
    # 宽松候选：行首可有 ( （ 或没有；数字后必须跟 ) ） . ．
    pat = re.compile(r"^[\s>]*[（(]?\s*(\d{1,2})\s*[）).．]\s*", re.M)
    cands = [(m.start(), m.end(), int(m.group(1))) for m in pat.finditer(text)]
    if not cands:
        return []

    # 用递增序列过滤：从第一个 1 开始，只接受「等于当前期望值」或「比它略大
    # 但仍在合理范围」的候选；跳号时把期望值前推。
    best, cur = [], []
    expect = 1
    for s, e, n in cands:
        if n == expect:
            cur.append((s, e, n)); expect += 1
        elif n > expect and n - expect <= 3:
            # 小幅跳号（源文件缺几题）：接受并前推
            cur.append((s, e, n)); expect = n + 1
        elif n == 1 and cur:
            # 出现新的 1 → 上一段结束（可能是下一节或重复）
            break
    return cur


def parse_exam(path):
    """解析真题卷 md，返回 {题号(int): 题干文本}。"""
    if not os.path.exists(path):
        return {}
    text = io.open(path, encoding="utf-8").read()
    starts = _find_starts(text)
    if not starts:
        return {}

    out = {}
    for i, (s, e, n) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        body = text[e:end]
        body = _strip_inline_solution(body)
        body = re.sub(r"^#.*$", "", body, flags=re.M)           # 节标题
        body = re.sub(r"^!\[.*?\]\(.*?\)\s*$", "", body, flags=re.M)  # 图片引用
        body = re.sub(r"[（(]本题满分\s*\d+\s*分[）)]", "", body)      # 小标题
        body = body.strip()
        if not body:
            continue
        if n not in out or len(body) > len(out[n]):
            out[n] = body
    return out


def load_local(year):
    """取某年本地真题文本（返回 {题号: 题干}），不可靠年份返回 {}。"""
    if year not in TEXT_OK_YEARS:
        return {}
    p = os.path.join(RAW, "%d年真题.md" % year)
    return parse_exam(p) if os.path.exists(p) else {}


def main():
    ap = argparse.ArgumentParser(description="给站点真题补文字题干")
    ap.add_argument("--write", action="store_true", help="写回 question.stem")
    ap.add_argument("--ocr-report", action="store_true", help="列出仍需 OCR 的题")
    ap.add_argument("--groups", nargs="*", default=["8", "9", "10"])
    args = ap.parse_args()

    ensure_dirs()
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row

    rows = [dict(r) for r in c.execute(
        "SELECT id, group_id, year, number, qtype_name, score, stem, img_local "
        "FROM question WHERE group_id IN (%s) ORDER BY group_id, CAST(number AS INT)"
        % ",".join("?" * len(args.groups)), args.groups)]

    cache = {}
    filled, need_ocr = [], []
    for r in rows:
        if (r["stem"] or "").strip():
            continue
        try:
            y = int(r["year"])
            n = int(r["number"])
        except (TypeError, ValueError):
            need_ocr.append(r); continue
        if r["group_id"] not in TEXT_GROUPS:
            need_ocr.append(r); continue
        if y not in cache:
            cache[y] = load_local(y)
        txt = cache[y].get(n)
        if txt:
            filled.append((r, txt))
        else:
            need_ocr.append(r)

    print("站点题 %d 道，本地文本可补 %d 道，需 OCR %d 道\n"
          % (len(rows), len(filled), len(need_ocr)))

    def tally(items, keyf):
        d = {}
        for it in items:
            d[keyf(it)] = d.get(keyf(it), 0) + 1
        return "  ".join("%s:%d" % kv for kv in sorted(d.items()))

    print("可补年份：", tally(filled, lambda t: t[0]["year"]))
    print("可补分组：", tally(filled, lambda t: t[0]["group_id"]))
    print("需OCR年份：", tally(need_ocr, lambda r: r["year"]))
    print("需OCR分组：", tally(need_ocr, lambda r: r["group_id"]))

    if args.ocr_report:
        print("\n--- 需 OCR 明细（前 40）---")
        for r in need_ocr[:40]:
            print("  %-10s g%-2s %s年 #%-3s %-4s img=%s"
                  % (r["id"], r["group_id"], r["year"], r["number"],
                     r["qtype_name"], r["img_local"]))
        out = os.path.join(DATA, "_ocr_todo.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump([{"id": r["id"], "group_id": r["group_id"], "year": r["year"],
                        "number": r["number"], "img": r["img_local"]}
                       for r in need_ocr], f, ensure_ascii=False, indent=1)
        print("\n共 %d 题，已写 %s" % (len(need_ocr), out))
        return

    if filled:
        print("\n--- 抽样 3 道 ---")
        for r, t in filled[:3]:
            print("  %s %s年#%s" % (r["id"], r["year"], r["number"]))
            print("     " + t.replace("\n", " / ")[:200])

    if args.write and filled:
        for r, t in filled:
            c.execute("UPDATE question SET stem=? WHERE id=?", (t, r["id"]))
        c.commit()
        print("\n已写库 %d 道。" % len(filled))
    elif not args.write:
        print("\n（预览模式；加 --write 写库）")


if __name__ == "__main__":
    main()



if __name__ == "__main__":
    main()
