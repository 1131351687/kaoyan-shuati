#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ocr_stems.py —— 题图 OCR 转 LaTeX（多通道自动降级）

背景：站点 1224 道真题只有题图（img_local），stem 全空；本地真题文本只能
可靠覆盖 2009–2020（见 tools/probe_stem_sources.py）。其余年份必须从图片识别。

通道实测（2026-10）：
  ✅ deepseek      api.deepseek.com        deepseek-chat       真视觉、0.6s、无配额限制
  ✅ chatanywhere  api.chatanywhere.tech   deepseek-v4.1-flash 真视觉、3.6s，但 100 次/24h 配额
  ❌ free          freeshare.cc.cd         上游 RemoteDisconnected / no_available_workers
  ❌ cc            api.codex666ai.com      502 upstream_error
  ❌ openrouter    该模型无 image endpoint

判定「真视觉」的方法见 tools/_vision_test.py：真图能复述题干、噪声图识别为噪点。

用法：
    python tools/ocr_stems.py --test 3             # 试跑 3 张看质量（不写库）
    python tools/ocr_stems.py --limit 20 --write   # 正式跑 20 张
    python tools/ocr_stems.py --all --write        # 全量（补 _ocr_todo.json 里仍空 stem 的）
    python tools/ocr_stems.py --ids 714 723        # 指定题目
"""
import argparse
import base64
import json
import os
import ssl
import sqlite3
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB, DATA, IMAGES, ensure_dirs  # noqa: E402

CRED = os.path.join(os.path.expanduser(os.environ.get("DSH_HOME", "~/.dsh")),
                    ".credentials.yaml")
TODO = os.path.join(DATA, "_ocr_todo.json")

# 按优先级排列的通道（都需支持 image_url）
CHANNELS = [
    ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    ("chatanywhere", "https://api.chatanywhere.tech/v1", "CHATANYWHERE_API_KEY",
     "deepseek-v4.1-flash"),
    ("free", "https://freeshare.cc.cd/v1", "FREE_API_KEY", "deepseek-v4.1-flash"),
]

SYS = """你是考研数学真题录入专家。把图片中的题目**完整、准确**转写成 Markdown + LaTeX。

要求：
1. 数学公式一律用 LaTeX：行内 $...$，独立成行用 $$...$$。
   分式用 \\frac{}{}，根式 \\sqrt{}，积分 \\int，极限 \\lim，求和 \\sum。
2. 保留原题号（如「(3)」「3.」）和全部选项，选项写作 (A) (B) (C) (D)。
3. 矩阵用 \\begin{pmatrix}...\\end{pmatrix}，分段函数用 \\begin{cases}...\\end{cases}。
4. 不要翻译、不要改写、不要解答，不要添加任何解释或前后缀文字。
5. 如果图中含函数图像而无法用文字表达，在对应位置写 [图]。
6. 只输出题目本身。"""


def creds():
    d = {}
    try:
        for line in open(CRED, encoding="utf-8"):
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return d


C = creds()
DEAD = set()          # 已判定不可用的通道，后续跳过


def pick_channel():
    for ch in CHANNELS:
        if ch[0] in DEAD:
            continue
        if os.environ.get(ch[2]) or C.get(ch[2]):
            return ch
    return None


def img_b64(path):
    b = open(path, "rb").read()
    ext = os.path.splitext(path)[1].lower()
    return base64.b64encode(b).decode(), ("image/jpeg" if ext in (".jpg", ".jpeg")
                                          else "image/png")


def _once(ch, path, timeout):
    name, base, keyenv, model = ch
    key = os.environ.get(keyenv) or C.get(keyenv)
    b64s, mime = img_b64(path)
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": [
                {"type": "text", "text": "转写这道题。"},
                {"type": "image_url",
                 "image_url": {"url": "data:%s;base64,%s" % (mime, b64s)}},
            ]},
        ],
        "temperature": 0,
        "max_tokens": 2048,
    }).encode()
    ctx = ssl.create_default_context(); ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        d = json.loads(r.read().decode())
    return (d["choices"][0]["message"].get("content") or "").strip()


def ocr(path, timeout=180):
    """依次尝试各通道；失败则标记该通道死亡并换下一个。"""
    last = None
    for _ in range(len(CHANNELS)):
        ch = pick_channel()
        if ch is None:
            break
        try:
            t = _once(ch, path, timeout)
            if t:
                return t, ch[0]
        except urllib.error.HTTPError as e:
            code = e.code
            txt = e.read().decode("utf-8", "replace")[:160]
            last = "HTTP %s %s" % (code, txt)
            # 429/5xx 视为该通道暂时不可用
            if code in (401, 402, 403, 429) or code >= 500:
                DEAD.add(ch[0])
                print("      [通道 %s 失效 → %s，切换下一个]" % (ch[0], last))
                continue
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:120])
            DEAD.add(ch[0])
            print("      [通道 %s 异常 → %s，切换下一个]" % (ch[0], last))
            continue
    raise RuntimeError(last or "所有通道均不可用")


def clean(t):
    if not t:
        return ""
    t = t.strip()
    if t.startswith("```"):
        t = "\n".join(l for l in t.split("\n") if not l.strip().startswith("```")).strip()
    for pre in ("题目：", "题目:", "以下是", "转写结果：", "OCR 结果：", "```markdown"):
        if t.startswith(pre):
            t = t[len(pre):].strip()
    return t


def main():
    ap = argparse.ArgumentParser(description="题图 OCR 转 LaTeX")
    ap.add_argument("--test", type=int, help="试跑 N 张（不写库）")
    ap.add_argument("--limit", type=int, help="最多处理 N 张")
    ap.add_argument("--all", action="store_true", help="处理 _ocr_todo.json 全部")
    ap.add_argument("--write", action="store_true", help="写回 question.stem")
    ap.add_argument("--ids", nargs="*", help="指定题目 id")
    ap.add_argument("--offset", type=int, default=0, help="从第 N 条开始（断点续跑）")
    args = ap.parse_args()

    ensure_dirs()
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row

    if args.ids:
        todo = []
        for i in args.ids:
            r = c.execute("SELECT img_local FROM question WHERE id=?", (i,)).fetchone()
            if r is None:
                print("警告：id=%s 不存在，跳过" % i); continue
            todo.append({"id": i, "img": r["img_local"]})
    elif os.path.exists(TODO):
        todo = json.load(open(TODO, encoding="utf-8"))
    else:
        todo = [dict(r) for r in c.execute(
            "SELECT id, img_local AS img FROM question WHERE TRIM(COALESCE(stem,''))=''")]

    # 断点续跑：跳过已写库的
    if args.write:
        before = len(todo)
        have = {r[0] for r in c.execute(
            "SELECT id FROM question WHERE TRIM(COALESCE(stem,''))<>''")}
        todo = [t for t in todo if t["id"] not in have]
        if before != len(todo):
            print("跳过已完成的 %d 条" % (before - len(todo)))

    if args.offset:
        todo = todo[args.offset:]
    if args.test:
        todo = todo[:args.test]; args.write = False
    if args.limit:
        todo = todo[:args.limit]

    ch = pick_channel()
    print("待 OCR %d 张，首选通道 %s，备用 %s\n"
          % (len(todo), ch[0] if ch else "无",
             ",".join(x[0] for x in CHANNELS if x is not ch)))

    ok = 0
    t0 = time.time()
    for i, t in enumerate(todo, 1):
        if not t.get("img"):
            print("  [%d/%d] %s 无题图记录，跳过" % (i, len(todo), t["id"])); continue
        p = os.path.join(IMAGES, t["img"].replace("/", os.sep))
        if not os.path.exists(p):
            print("  [%d/%d] %s 图片缺失" % (i, len(todo), t["id"])); continue
        try:
            txt, used = ocr(p)
            txt = clean(txt)
        except Exception as e:
            print("  [%d/%d] %s 失败 %s" % (i, len(todo), t["id"], e)); continue
        if not txt:
            print("  [%d/%d] %s 空结果" % (i, len(todo), t["id"])); continue
        ok += 1
        print("  [%d/%d] %s ✔ %d 字符 (%s)" % (i, len(todo), t["id"], len(txt), used))
        if i <= 3 or args.test:
            print("      " + txt.replace("\n", " / ")[:260])
        if args.write:
            c.execute("UPDATE question SET stem=? WHERE id=?", (txt, t["id"]))
            c.commit()

    dt = time.time() - t0
    print("\n完成 %d/%d 张，耗时 %.0f 秒（%.1f 秒/张）"
          % (ok, len(todo), dt, dt / max(1, len(todo))))
    if not args.write:
        print("（未写库；加 --write）")


if __name__ == "__main__":
    main()
