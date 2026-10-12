# -*- coding: utf-8 -*-
"""冒烟测试：把所有 CLI 子命令跑一遍，确认搬迁后仍可用。

用法：python tools/smoke_cli.py
"""
import os, subprocess, sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
from paths import PYTHON                  # noqa: E402

BANK = os.path.join(PROJ, "app", "bank.py")
PY = PYTHON                               # 由 paths.py 自动探测，不再写死盘符
ANKI_OUT = os.path.join(PROJ, "docs", "_smoke_anki.txt")

CASES = [
    ["stats"],
    ["weak", "--top", "5"],
    ["hard", "--top", "5"],
    ["list", "--group", "8", "--limit", "3"],
    ["list", "--group", "mock2026", "--limit", "3"],
    ["list", "--group", "old1997", "--limit", "3"],
    ["list", "--hard", "--limit", "4"],
    ["list", "--kp", "二次型", "--limit", "3"],
    ["show", "mock2026-1-2"],
    ["show", "old2005-3"],
    ["show", "1942"],
    ["paper", "-n", "8", "--level", "easy", "--seed", "1"],
    ["paper", "-n", "6", "--level", "hard", "--seed", "2"],
    ["paper", "-n", "6", "--level", "mixed", "--seed", "3"],
    ["export", "--anki", ANKI_OUT],
]

env = dict(os.environ, PYTHONIOENCODING="utf-8")
ok = fail = 0
for case in CASES:
    p = subprocess.run([PY, BANK] + case, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    label = " ".join(case)
    if p.returncode == 0:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  FAIL {label}  exit={p.returncode}")
        tail = (p.stderr or p.stdout or "").strip().split("\n")[-4:]
        for ln in tail:
            print(f"         {ln}")

print(f"\n通过 {ok} / 失败 {fail}  (共 {len(CASES)})")
sys.exit(1 if fail else 0)
