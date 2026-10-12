# -*- coding: utf-8 -*-
"""
修复模拟卷题干乱码。

根因：源文件 `2026 李艳芳 三套卷 试题.md` 是 PDF 双栏版式转文本的产物，
第一套第 (2) 题的分段函数（一个 2x2 表格 + cases 环境）在转换时被拆碎，
与正文句子交错拼接，导致语序错乱：

  $x _ { n } = { \\left\\{ \\begin{array}... } & { n } \\\\ { 1 + x _ { n - 1 } } \\\\ { 2 } , \\right.} & { n } \\end{array}  $
  为偶数，（2）设 $x _ { 0 } = a \\neq 1$ ，对n≥1，令 则下列关于数列 ... 的说法中，正为奇数，确的是（）

正确读法（逐块还原）：
  - 表格第 1 行：(x_{n-1}+x_{n-2})/2 ,  n 为偶数
  - 表格第 2 行：1 + x_{n-1}/2 ,        n 为奇数
  - 句子：设 x_0 = a ≠ 1，对 n≥1，令 <上式>，则下列关于数列 {x_n} 的说法中，正确的是（）

这个脚本把源 md 里那一段替换为重建后的规范题干，并同步修正题号被吞掉的问题
（原行以 "为偶数，（2）设" 开头，(2) 混在正文里，所以 import_mock 把 (1)、(2) 粘成了一道题）。
"""
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import STUDY_MOCK          # noqa: E402

SRC = os.path.join(STUDY_MOCK, "2026 李艳芳 三套卷 试题.md")
BAK = SRC + ".bak"

# 重建后的第 (2) 题（markdown + LaTeX）
FIXED_Q2 = (
    "（2）设 $x _ { 0 } = a \\neq 1$ ，对 $n \\geq 1$ ，令\n\n"
    "$$x _ { n } = \\begin{cases}\n"
    "\\dfrac { x _ { n - 1 } + x _ { n - 2 } } { 2 } , & n \\text{ 为偶数} \\\\[6pt]\n"
    "1 + \\dfrac { x _ { n - 1 } } { 2 } , & n \\text{ 为奇数}\n"
    "\\end{cases}$$\n\n"
    "则下列关于数列 $\\left\\{ x _ { n } \\right\\}$ 的说法中，正确的是（）\n\n"
    "（A） $\\lim\\limits _ { n \\to \\infty } x _ { 2 n }$ , $\\lim\\limits _ { n \\to \\infty } x _ { 2 n + 1 }$ 均不存在.\n"
    "（B） $\\lim\\limits _ { n \\to \\infty } x _ { 2 n }$ , $\\lim\\limits _ { n \\to \\infty } x _ { 2 n + 1 }$ 均存在，但不相等.\n"
    "（C） $\\lim\\limits _ { n \\to \\infty } x _ { n }$ 存在，其值与 $a$ 有关.\n"
    "（D） $\\lim\\limits _ { n \\to \\infty } x _ { n }$ 存在，其值与 $a$ 无关.\n"
)


def main():
    if not os.path.exists(SRC):
        print(f"[X] 源文件不存在: {SRC}")
        return 1

    text = open(SRC, encoding="utf-8").read()
    orig_len = len(text)

    # 定位被破坏的那一行：同时含 "为偶数" 与 "确的是" 且以 "$x _ { n }" 开头
    lines = text.split("\n")
    hit = None
    for i, ln in enumerate(lines):
        if "为偶数" in ln and "确的是" in ln and "$x _ { n }" in ln:
            hit = i
            break

    if hit is None:
        print("[=] 未找到需要修复的乱码行（可能已修复过）")
        return 0

    print(f"[*] 命中第 {hit+1} 行，原长度 {len(lines[hit])} 字符")

    if not os.path.exists(BAK):
        shutil.copy2(SRC, BAK)
        print(f"[*] 已备份 -> {BAK}")

    # 该行前面是第 (1) 题（完好），后面紧跟第 (3) 题。
    # 把这一行整行替换为重建的 (2) 题。
    lines[hit] = FIXED_Q2
    text2 = "\n".join(lines)

    open(SRC, "w", encoding="utf-8").write(text2)
    print(f"[+] 已修复: {orig_len} -> {len(text2)} 字符")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
