#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""probe_stem_sources.py —— 检查各年本地真题文本的可用质量。

站点题要补文字题干，但本地「真题\原始卷\*.md」质量参差：
  · 2009–2021：格式规整（(n) 题号 + (A)(B)(C)(D)），质量好
  · 2022：OCR 严重损坏（正文变成乱码 `# -,<EFBFBD>:ll<EFBFBD>`），不可用
  · 2023：格式尚可（裸 1. 题号），LaTeX 有空格注入
  · 2024：题号错位（"则1." 把号粘在题干尾），选项错乱，不可用
  · 2025：干净但内联了解析

本脚本打分，决定「用文本」还是「走 OCR」。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import STUDY_ZHENTI  # noqa: E402


def score_file(path):
    t = io.open(path, encoding="utf-8").read()
    n = len(t)
    if n < 800:
        return 0.0, {"reason": "too_short"}

    # 1) 乱码率：非 ASCII、非中日韩、非常见数学符号的可疑字符
    bad = len(re.findall(r"[\ufffd\u25a1]", t))          # 替换符/方框
    # OCR 乱码特征：拉丁字母与符号混杂的无意义串，如 "-,�:ll�"
    garble = len(re.findall(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", t))
    ctrl_ratio = (bad + garble) / max(1, n)

    # 2) 题号可识别性：行首 (n) 或 n. 或 n． 或 【n】
    paren = len(re.findall(r"^[\s>]*[（(]\d{1,2}[）)]", t, re.M))
    dot = len(re.findall(r"^[\s>]*\d{1,2}\s*[.．]", t, re.M))
    brack = len(re.findall(r"^[\s>]*【\d{1,2}】", t, re.M))
    # 题号错位特征：数字紧贴「则」「，」等结尾词
    misplaced = len(re.findall(r"[\u4e00-\u9fff]\s*\d{1,2}\s*[.．]\s*$", t, re.M))

    # 3) 选项完整性
    opts = len(re.findall(r"^[\s]*[（(]?[ABCD][）)]?\s*[.．、]", t, re.M))

    # 4) LaTeX 空格注入（\mathrm { e } 这种）
    spaced_tex = len(re.findall(r"\\[a-zA-Z]+\s+\{", t))

    s = 1.0
    if ctrl_ratio > 0.001:
        s -= 0.6
    if paren == 0 and brack == 0 and dot < 5:
        s -= 0.4
    # 题号错位（"则1." 这类把题号粘在题干尾巴）是硬伤：无法可靠切分题号
    if misplaced >= 2:
        s -= 0.4
    elif misplaced == 1:
        s -= 0.1
    if opts < 10:
        s -= 0.3
    if spaced_tex > 40:
        s -= 0.2
    s = max(0.0, min(1.0, s))
    return s, {"chars": n, "ctrl_ratio": round(ctrl_ratio, 5), "paren": paren,
               "dot": dot, "bracket": brack, "misplaced": misplaced,
               "opts": opts, "spaced_tex": spaced_tex}


def main():
    print("%-6s %5s %8s %6s %5s %5s %6s %6s %6s  %s"
          % ("年", "分", "字符", "乱码", "(n)", "n.", "【n】", "错位", "选项", "结论"))
    good, bad = [], []
    for y in range(1987, 2027):
        p = os.path.join(STUDY_ZHENTI, "%d年真题.md" % y)
        if not os.path.exists(p):
            continue
        s, m = score_file(p)
        verdict = "✅ 可用文本" if s >= 0.6 else "❌ 走 OCR"
        (good if s >= 0.6 else bad).append(y)
        print("%-6d %5.2f %8d %6.4f %5d %5d %6d %6d %6d  %s"
              % (y, s, m["chars"], m["ctrl_ratio"], m["paren"], m["dot"],
                 m["bracket"], m["misplaced"], m["opts"], verdict))
    print("\n可用文本年份 (%d): %s" % (len(good), good))
    print("需 OCR 年份 (%d): %s" % (len(bad), bad))
    # 站点覆盖是 2009-2026
    site = [y for y in good if 2009 <= y <= 2026]
    need = [y for y in range(2009, 2027) if y not in site]
    print("\n站点范围 2009-2026 内：")
    print("  可用本地文本 (%d 年): %s" % (len(site), site))
    print("  需 OCR      (%d 年): %s" % (len(need), need))


if __name__ == "__main__":
    main()
