# -*- coding: utf-8 -*-
"""扫描脚本里的硬编码路径（整理项目时用）。

用法：python tools/_scan_paths.py
"""
import os, re, glob

TOOLS = os.path.dirname(os.path.abspath(__file__))
PAT = re.compile(r"D:\\try\\(?:bank|tools|capture|zhentiqiang|考研刷题)[^\s\"']*")

n = 0
for f in sorted(glob.glob(os.path.join(TOOLS, "*.py")) + glob.glob(os.path.join(TOOLS, "*.js"))):
    name = os.path.basename(f)
    if name in ("paths.py", "_scan_paths.py"):
        continue
    t = open(f, encoding="utf-8", errors="replace").read()
    ms = sorted(set(m.group(0) for m in PAT.finditer(t)))
    if ms:
        n += 1
        print(name)
        for m in ms:
            print("   ", m)
print(f"\n共 {n} 个脚本仍含旧路径")
