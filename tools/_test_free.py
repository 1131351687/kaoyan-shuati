# -*- coding: utf-8 -*-
"""_test_free.py —— 直接测 free 通道是否还活着（含耗时）。"""
import json
import os
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_difficulty import FREE_BASE, FREE_MODEL, _free_key  # noqa: E402

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

body = json.dumps({
    "model": FREE_MODEL,
    "messages": [{"role": "user", "content": "只回复数字：3+4=?"}],
    "temperature": 0,
    "max_tokens": 16,
}).encode()

req = urllib.request.Request(
    FREE_BASE.rstrip("/") + "/chat/completions", data=body,
    headers={"Content-Type": "application/json",
             "Authorization": "Bearer " + _free_key()},
    method="POST")

t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        d = json.loads(r.read().decode())
    print("✅ free 通道正常  %.1fs  reply=%r  model=%s"
          % (time.time() - t0, d["choices"][0]["message"]["content"], d.get("model")))
except Exception as e:
    print("❌ free 通道 %.1fs  %s: %s" % (time.time() - t0, type(e).__name__, e))
