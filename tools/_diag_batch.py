# -*- coding: utf-8 -*-
"""诊断：为什么某些批 parse_verdicts 返回 0。

打印原始回复的长度、结尾、finish_reason，判断是截断还是格式异常。
"""
import json
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_solution_filter as F


def call_raw(prompt, max_tokens=8192):
    key = F._key("AGNES_API_KEY")
    body = json.dumps({
        "model": F.AGNES_MODEL,
        "messages": [{"role": "system", "content": F.SYS},
                     {"role": "user", "content": prompt}],
        "temperature": 0, "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        F.AGNES_BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


rows = F.load_candidates(limit=None)
for bi in (0, 1, 3):
    batch = rows[bi * 12:(bi + 1) * 12]
    if not batch:
        continue
    print(f"\n===== 批 {bi+1}  ({len(batch)} 条) =====")
    try:
        d = call_raw(F.build_prompt(batch))
    except Exception as e:                          # noqa: BLE001
        print("  调用异常:", str(e)[:200])
        continue
    ch = d["choices"][0]
    txt = ch["message"]["content"]
    print(f"  finish_reason = {ch.get('finish_reason')!r}")
    print(f"  usage = {d.get('usage')}")
    print(f"  回复长度 = {len(txt)}")
    print(f"  结尾 120 字 = {txt[-120:]!r}")
    v = F.parse_verdicts(txt, len(batch))
    print(f"  parse_verdicts -> {len(v)} 项")
