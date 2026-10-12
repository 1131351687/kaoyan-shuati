# -*- coding: utf-8 -*-
r"""
Locate the group id of the English question bank.

Earlier probing (groups 1-80) returned empty `papers` for everything except
8/9/10. The English page /kaoyan/english exists, so the group likely lives
outside 1..80, or the papers only appear for the group the front-end selects.

Strategy:
  1. Read the English page HTML and its JS for any hard-coded group id.
  2. Try to drive the site's own search/filter endpoint if one exists.
  3. Probe a wider id range (1..400) plus common non-numeric ids.
  4. Also probe /api/papers/<gid> and /api/papergroup_stats/<gid> because a
     group may hide its papers in bootstrap but expose them elsewhere.
"""
import json
import os
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import CAPTURE, ensure_dirs  # noqa: E402

BASE = "https://zhentiqiang.com"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def req(path, timeout=15):
    r = urllib.request.Request(BASE + path, headers={
        "User-Agent": UA, "Accept": "*/*", "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE + "/kaoyan/english",
    })
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        return resp.getcode(), resp.read().decode("utf-8", "replace")


def jget(path):
    try:
        code, body = req(path)
        if code != 200:
            return code, None
        return code, json.loads(body)
    except Exception as e:
        return -1, {"err": str(e)}


def probe_group(gid):
    """Return a short description if the group has content."""
    code, d = jget("/api/bootstrap?group_id=%s" % gid)
    if not isinstance(d, dict) or not d.get("success"):
        return None
    papers = d.get("papers") or []
    pg = d.get("papergroups") or []
    tree = d.get("knowledge_tree") or []
    if not papers and not pg:
        return None
    names = [p.get("name") or p.get("paper_name") or "" for p in pg]
    return {"gid": str(gid), "n_papers": len(papers), "n_pg": len(pg),
            "n_tree": len(tree), "names": names[:4]}


def scan_html_for_groups():
    """Look for group ids hard-coded in the English page."""
    hits = {}
    for page in ("/kaoyan/english", "/", "/kaoyan/math"):
        try:
            code, html = req(page)
        except Exception as e:
            hits[page] = {"err": str(e)}
            continue
        found = set()
        import re
        for m in re.finditer(r"(?:group_id|groupId|group)\s*[:=]\s*['\"]?(\d{1,4})", html):
            found.add(m.group(1))
        for m in re.finditer(r"/api/(?:bootstrap|papers|knowledge_tree)/(\d{1,4})", html):
            found.add(m.group(1))
        hits[page] = {"len": len(html), "ids": sorted(found, key=int)}
    return hits


def main():
    ensure_dirs()
    out = {"html_scan": scan_html_for_groups(), "found": [], "checked": 0}

    ids = [str(i) for i in range(1, 401)]
    for gid in ids:
        out["checked"] += 1
        info = probe_group(gid)
        if info:
            out["found"].append(info)
            print("  HIT group %s: %d papers, %d papergroups, %s"
                  % (gid, info["n_papers"], info["n_pg"], info["names"]), flush=True)
        if out["checked"] % 50 == 0:
            print("  ... checked %d" % out["checked"], flush=True)
            time.sleep(0.5)

    print("\n=== html scan ===")
    print(json.dumps(out["html_scan"], ensure_ascii=False, indent=1))
    print("\n=== groups with content ===")
    for f in out["found"]:
        print("  %s -> %d papers %s" % (f["gid"], f["n_papers"], f["names"]))

    p = os.path.join(CAPTURE, "_english_group_scan.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("\nsaved:", p)


if __name__ == "__main__":
    main()
