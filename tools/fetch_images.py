# -*- coding: utf-8 -*-
r"""把 1224 张题图全部下载到本地，按 group/paper/题号 组织。
输出结构：
  IMAGES\group_8\paper_99\1.png
"""
import os
import re
import sqlite3
import ssl
import time
import sys
import urllib.request
from paths import APP, IMAGES, TOOLS, ZTQ_DB

BASE = "https://zhentiqiang.com"
DB = ZTQ_DB
OUT = IMAGES

# 站点证书链在本机 schannel 下有故障，用 Python 自己的 TLS 栈并关闭校验
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(path, retries=3):
    url = BASE + path if path.startswith("/") else path
    last = None
    for a in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": BASE + "/kaoyan/math",
                "Accept": "image/avif,image/webp,image/png,*/*",
            })
            with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
                return r.read(), r.status
        except Exception as e:
            last = e
            time.sleep(0.6 * (a + 1))
    raise last


def main():
    only = None
    if len(sys.argv) > 1 and sys.argv[1] == "--group":
        only = sys.argv[2]

    c = sqlite3.connect(DB)
    q = "SELECT id, group_id, paper_name, number, full_image FROM question WHERE full_image<>''"
    args = ()
    if only:
        q += " AND group_id=?"
        args = (only,)
    rows = c.execute(q + " ORDER BY group_id, CAST(paper_name AS INT) DESC, CAST(number AS INT)", args).fetchall()

    os.makedirs(OUT, exist_ok=True)
    ok = skip = fail = 0
    bad = []
    t0 = time.time()

    for i, (qid, gid, paper, num, img) in enumerate(rows, 1):
        # 目标路径：还原站点的静态目录结构，但去掉 /static/photos/ 前缀
        rel = re.sub(r"^/static/photos/", "", img).strip("/")
        dst = os.path.join(OUT, rel.replace("/", os.sep))

        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            skip += 1
            continue

        try:
            data, st = fetch(img)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
            ok += 1
        except Exception as e:
            fail += 1
            bad.append((qid, img, str(e)[:80]))
            print(f"  ! qid={qid} {img} -> {str(e)[:80]}")

        if i % 100 == 0:
            el = time.time() - t0
            print(f"  [{i}/{len(rows)}] 新下={ok} 跳过={skip} 失败={fail} "
                  f"{i/el:.1f} req/s 已用{el:.0f}s")

    el = time.time() - t0
    print("\n" + "=" * 70)
    print(f"题图下载完成: 新下载={ok} 已存在={skip} 失败={fail}  用时{el:.0f}s")
    tot = sum(len(f) for _, _, f in os.walk(OUT))
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(OUT) for f in fs)
    print(f"本地文件总数={tot}  总大小={size/1024/1024:.1f} MB")
    if bad:
        print("\n失败清单(前20):")
        for b in bad[:20]:
            print("  ", b)
        with open(r"APP\download_errors.txt", "w", encoding="utf-8") as f:
            for b in bad:
                f.write(f"{b[0]}\t{b[1]}\t{b[2]}\n")


if __name__ == "__main__":
    main()
