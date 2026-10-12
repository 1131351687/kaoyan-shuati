# -*- coding: utf-8 -*-
"""_test_channels.py —— 逐个测所有可用通道的文本补全可用性。"""
import json
import os
import ssl
import time
import urllib.error
import urllib.request

CRED = os.path.join(os.environ.get("DSH_HOME", r"C:\Users\XP\.dsh"),
                    ".credentials.yaml")

CHANNELS = [
    ("free", "https://freeshare.cc.cd/v1", "FREE_API_KEY", "deepseek-v4.1-flash"),
    ("chatanywhere", "https://api.chatanywhere.tech/v1", "CHATANYWHERE_API_KEY",
     "deepseek-v4.1-flash"),
    ("gpt", "https://api.openai.com/v1", "GPT_API_KEY", "gpt-4o-mini"),
    ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    ("cc", "https://api.codex666ai.com/v1", "CC_API_KEY", "deepseek-v4.1-flash"),
]


def creds():
    d = {}
    try:
        for line in open(CRED, encoding="utf-8"):
            line = line.strip()
            if ":" in line and not line.startswith("#") and not line.startswith("refs"):
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return d


C = creds()
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

for name, base, keyenv, model in CHANNELS:
    key = os.environ.get(keyenv) or C.get(keyenv)
    if not key:
        print("%-14s 跳过（无 %s）" % (name, keyenv)); continue
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "只回复数字：3+4=?"}],
                       "temperature": 0, "max_tokens": 16}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key},
                                 method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
            d = json.loads(r.read().decode())
        print("%-14s ✅ %.1fs  %r" % (name, time.time() - t0,
                                     d["choices"][0]["message"]["content"][:40]))
    except urllib.error.HTTPError as e:
        print("%-14s ❌ %.1fs HTTP %s %s" % (name, time.time() - t0, e.code,
                                            e.read().decode("utf-8", "replace")[:120]))
    except Exception as e:
        print("%-14s ❌ %.1fs %s: %s" % (name, time.time() - t0, type(e).__name__, e))
