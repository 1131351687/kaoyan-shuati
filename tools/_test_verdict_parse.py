# -*- coding: utf-8 -*-
"""parse_verdicts 的形态单测（不写库、不联网）。"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_solution_filter import parse_verdicts as P

ARR = json.dumps([{"i": 0, "ok": True, "conf": 0.9, "why": "x"},
                  {"i": 1, "ok": False, "conf": 0.8, "why": "y"}],
                 ensure_ascii=False)

cases = [
    ("裸数组", ARR, 2),
    ("单引号包裹（agnes 实测形态）", "'" + ARR + "'", 2),
    ("双引号包裹（双重编码）", json.dumps(ARR, ensure_ascii=False), 2),
    ("markdown 围栏", "```json\n" + ARR + "\n```", 2),
    ("前后有解释文字", "好的，结果如下：\n" + ARR + "\n以上。", 2),
    # 截断抢救：第 2 项写到一半就停在 `}` 之前
    ("截断（末尾对象残缺）",
     '[{"i":0,"ok":true,"conf":0.9,"why":"a"},{"i":1,"ok":false,"conf":0.8,"wh', 1),
    ("截断（末尾对象已闭合但缺 ]）",
     '[{"i":0,"ok":true,"conf":0.9,"why":"a"},{"i":1,"ok":false,"conf":0.8,"why":"b"}', 2),
    ("垃圾输入", "抱歉，我无法判断这些内容。", 0),
    ("空输入", "", 0),
    # 单条时 agnes 返回裸对象而不是数组（实测于 2015#15）
    ("单对象（batch=1 实测形态）", '{\n "i": 0,\n "ok": false,\n "conf": 0.9,\n "why": "z"\n}', 1),
]

bad = 0
for name, txt, exp in cases:
    got = P(txt, 2)
    ok = len(got) == exp
    bad += 0 if ok else 1
    print(f"  {'OK ' if ok else 'X  '} {name:26s} -> {len(got)} 项 (期望 {exp})  {got}")

print("\n全部通过" if not bad else f"\n{bad} 项失败")
raise SystemExit(1 if bad else 0)
