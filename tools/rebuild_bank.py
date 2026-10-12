# -*- coding: utf-8 -*-
"""一键重建本地题库（幂等，按依赖顺序串起全部步骤）。

顺序很重要：
  1. build_bank.py      从 ztq.db 生成 ztq_bank.db + index.json（会先删库！）
  2. import_answers.py  回填本地解析答案（必须在 1 之后）
  3. import_mock.py     导入模拟卷（必须在 1 之后）
  4. export_index.py    把回填/模拟卷结果重新导出到 index.json（必须在 2、3 之后）

以后再跑任何“重新生成题库”的操作，都应调用本脚本，而不是单独跑 build_bank.py。
"""
import os
import subprocess
import sys
from paths import TOOLS, PYTHON

PY = PYTHON          # 解释器由 paths.py 自动探测，不再写死盘符
T = TOOLS
STEPS = [
    ("build_bank.py", "从 ztq.db 生成题库骨架（会先删库）"),
    ("import_answers.py", "回填本地解析答案（group 8）"),
    ("import_mock.py", "导入李艳芳预测三套卷（9 套 → mock2024/2025/2026）"),
    ("export_index.py", "重新导出 index.json（含回填与模拟卷）"),
]


def run(script, desc):
    print(f"\n{'='*66}\n>>> {script} — {desc}\n{'='*66}")
    r = subprocess.run([PY, os.path.join(T, script)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    out = (r.stdout or "").strip()
    if out:
        # 只打印最后 12 行，避免刷屏
        for ln in out.split("\n")[-12:]:
            print("   " + ln)
    if r.returncode != 0:
        print(f"   !! 退出码 {r.returncode}")
        print((r.stderr or "")[-800:])
        return False
    return True


if __name__ == "__main__":
    print("一键重建本地题库（D:\\try\\bank）")
    for s, d in STEPS:
        if not run(s, d):
            print(f"\n✗ 在 {s} 失败，已中止。")
            sys.exit(1)
    print("\n✓ 全部步骤完成。")
