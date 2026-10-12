# -*- coding: utf-8 -*-
"""
probe.py —— 对 https://zhentiqiang.com/kaoyan/math 的只读侦察脚本
目的：绕开本机 schannel/PowerShell 的 TLS 凭据故障，用 Python 自带 TLS 栈抓页面，
      随后枚举 JS/接口，定位题库数据来源。
"""
import os
import re
import ssl
import sys
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse

BASE = "https://zhentiqiang.com"
TARGET = BASE + "/kaoyan/math"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "capture")
os.makedirs(OUT, exist_ok=True)
os.makedirs(os.path.join(OUT, "js"), exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
    "Connection": "close",
}


def make_ctx():
    ctx = ssl.create_default_context()
    # 沙箱里先关闭证书校验以获得响应；正式使用时建议恢复校验
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except Exception:
        pass
    return ctx


CTX = make_ctx()


def get(url, extra=None, timeout=25, binary=False):
    h = dict(HEADERS)
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        body = r.read()
        return {
            "url": url,
            "status": getattr(r, "status", r.getcode()),
            "elapsed": round(time.time() - t0, 3),
            "headers": dict(r.headers),
            "body": body if binary else body.decode("utf-8", "ignore"),
            "len": len(body),
        }


def save(name, text):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def main():
    print("=" * 70)
    print("[1] 拉取目标页:", TARGET)
    try:
        r = get(TARGET)
    except Exception as e:
        print("  失败:", type(e).__name__, e)
        return 1

    print("  HTTP", r["status"], "| len =", r["len"], "| %.2fs" % r["elapsed"])
    print("  server:", r["headers"].get("Server"))
    for k in ("Set-Cookie", "Content-Type", "X-Powered-By"):
        if k in r["headers"]:
            print(f"  {k}: {r['headers'][k]}")
    save("page.html", r["body"])

    html = r["body"]
    print("\n[2] 资源枚举")
    js = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I)
    css = re.findall(r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', html, re.I)
    print("  scripts:", len(js), "| css:", len(css))
    for u in js:
        print("   JS:", u)
    for u in css:
        print("   CSS:", u)

    print("\n[3] 页面内联线索（接口/密钥/框架）")
    patterns = {
        "api_path": r'["\'](/[a-zA-Z0-9_\-/]*(?:api|api/|question|exam|paper|list|detail|bank)[a-zA-Z0-9_\-/\.]*)["\']',
        "abs_url": r'https?://[a-zA-Z0-9\.\-]+/[a-zA-Z0-9_\-/\.]*',
        "framework": r'(vue|react|angular|next|nuxt|jquery|axios|element-ui|antd)',
        "token_key": r'(token|secret|appid|appKey|sign|accessKey)["\']?\s*[:=]',
    }
    hits = {}
    for k, pat in patterns.items():
        found = sorted(set(re.findall(pat, html, re.I)))
        hits[k] = found[:60]
        print(f"  [{k}] {len(found)} hits")
        for x in found[:25]:
            print("     -", x)
    save("hits.json", json.dumps(hits, ensure_ascii=False, indent=2))

    print("\n[4] 抽样下载前端 JS 并搜索接口特征")
    api_found = set()
    for u in js[:12]:
        full = urljoin(TARGET, u)
        try:
            rr = get(full)
        except Exception as e:
            print("   ! 下载失败", full, type(e).__name__)
            continue
        name = re.sub(r'[^\w\.\-]', '_', os.path.basename(urlparse(full).path)) or "chunk.js"
        save("js/" + name, rr["body"])
        txt = rr["body"]
        for m in re.findall(r'["\'`](/[a-zA-Z0-9_\-/]*(?:api|question|exam|paper|list|detail|bank|kaoyan)[a-zA-Z0-9_\-/\.]*)["\'`]', txt):
            api_found.add(m)
        for m in re.findall(r'baseURL\s*[:=]\s*["\'`]([^"\'`]+)["\'`]', txt):
            api_found.add("BASEURL=" + m)
        print(f"   ~ {full} ({rr['len']}B)")

    print("\n[5] 汇总候选接口路径")
    for p in sorted(api_found):
        print("   ->", p)
    save("api_candidates.txt", "\n".join(sorted(api_found)))

    print("\n完成，产物目录:", os.path.abspath(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
