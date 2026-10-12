# -*- coding: utf-8 -*-
"""_vision_test.py —— 判定各通道是真看图还是瞎猜。

方法：同一张题图问两次 —— 一次正常问「答案是什么」，
一次把图片换成纯噪声但仍声称是题图。若两次回答一致，说明没真看图。
再加一个「描述图中算式」的开放题，真 vision 能说出内容。
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import IMAGES                  # noqa: E402

CRED = os.path.join(os.environ.get("DSH_HOME", r"C:\Users\XP\.dsh"),
                    ".credentials.yaml")
IMG = os.path.join(IMAGES, "group_8", "paper_79", "1.png")
# 真图 + 一张纯白噪声图（同尺寸），噪声图放在项目外的临时目录
NOISE_IMG = os.path.join(os.environ.get("TEMP") or "/tmp", "_noise.png")


def creds():
    d = {}
    for line in open(CRED, encoding="utf-8"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip().strip("'\"")
    return d


def b64(p):
    return base64.b64encode(open(p, "rb").read()).decode()


def ask(base, key, model, img_b64, prompt, timeout=90):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img_b64}}]}],
        "temperature": 0, "max_tokens": 300}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key},
                                 method="POST")
    ctx = ssl.create_default_context(); ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            d = json.loads(r.read().decode())
        return "OK: " + (d["choices"][0]["message"].get("content") or "")[:200]
    except urllib.error.HTTPError as e:
        return "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:150])
    except Exception as e:
        return "%s: %s" % (type(e).__name__, str(e)[:150])


if __name__ == "__main__":
    # 造噪声图
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(IMG)
        a = (np.random.rand(im.height, im.width, 3) * 255).astype("uint8")
        Image.fromarray(a).save(NOISE_IMG)
        print("噪声图已生成", im.size)
    except Exception as e:
        print("噪声图生成失败:", e); raise SystemExit(1)

    C = creds()
    Q_DESC = "这张图片里有什么？如果是数学题，请写出你能看到的公式。"
    tests = [
        ("deepseek-chat", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    ]
    for model, base, ke in tests:
        k = C.get(ke)
        if not k:
            print("跳过", ke); continue
        print("=" * 70)
        print("通道 %s / %s" % (base, model))
        print("  [真图·描述] ", ask(base, k, model, b64(IMG), Q_DESC)[:220])
        print("  [噪声·描述]", ask(base, k, model, b64(NOISE_IMG), Q_DESC)[:220])
