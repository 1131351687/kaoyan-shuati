# -*- coding: utf-8 -*-
"""_verify_ocr.py —— 用「题目答案」交叉验证 OCR 是否正确。

思路：站点选择题自带 answer（A/B/C/D）。若 OCR 出的题干里能解析出选项，
且该题答案是 D，那么 OCR 出的 (D) 内容应当是非空且合语法的一段。
更强的验证：拿 OCR 出的题干去问 LLM「这道题答案是什么」，与站点答案比对。
命中率高 ⇒ OCR 可信。
"""
import json
import os
import random
import re
import ssl
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_difficulty import FREE_BASE, FREE_MODEL, _free_key  # noqa: E402
from paths import BANK_DB                 # noqa: E402

DB = BANK_DB
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10


def ask(prompt, timeout=120):
    body = json.dumps({"model": FREE_MODEL,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 64}).encode()
    ctx = ssl.create_default_context(); ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(FREE_BASE.rstrip("/") + "/chat/completions",
                                 data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + _free_key()},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        d = json.loads(r.read().decode())
    return (d["choices"][0]["message"].get("content") or "").strip()


def main():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT id, year, number, qtype_name, answer, stem FROM question "
        "WHERE qtype_name='选择题' AND answer IN ('A','B','C','D') "
        "AND TRIM(COALESCE(stem,''))<>'' AND group_id IN ('8','9','10')")]
    random.seed(7)
    pick = random.sample(rows, min(N, len(rows)))
    print("抽样 %d 道已 OCR 的站点选择题，用 LLM 重做并与站点答案比对\n" % len(pick))

    hit = 0
    for r in pick:
        q = ("这是一道考研数学选择题。请只回答一个字母（A/B/C/D），不要任何解释。\n\n"
             + r["stem"][:1500])
        try:
            a = ask(q)
        except Exception as e:
            print("  %s 调用失败 %s" % (r["id"], e)); continue
        got = re.search(r"[ABCD]", a.upper())
        got = got.group(0) if got else "?"
        ok = (got == r["answer"])
        hit += ok
        print("  %-6s %s年#%-3s 站点=%s LLM=%s %s" % (
            r["id"], r["year"], r["number"], r["answer"], got,
            "✅" if ok else "❌"))
    print("\n命中 %d/%d = %.0f%%" % (hit, len(pick), 100.0 * hit / max(1, len(pick))))


if __name__ == "__main__":
    main()
