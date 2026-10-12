# -*- coding: utf-8 -*-
r"""make_skeleton.py —— 从现有题库副本，抽出一个「干净框架版」。

产出 D:\try\刷题框架\：
  ✅ 保留：全部代码（app/ tools/）、KaTeX、README、
           空数据库（只有表结构）、空的 index.json（只有框架字段）
  ❌ 剔除：题图、题目、答案、学习记录、题目标签、抓包样本、
           一切已导入的资料与抓取证据

用法：
    python tools/make_skeleton.py                 # 默认目标 D:\try\刷题框架
    python tools/make_skeleton.py --dest D:\x     # 指定目标
    python tools/make_skeleton.py --with-capture  # 连 capture/ 抓包证据一起保留
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import PROJ, BANK_DB, APP, DATA, TOOLS, CAPTURE, DOCS   # noqa: E402

SKIP_DIR_NAMES = {"__pycache__", ".git", "_build", "_archive", "images", "mock"}
SKIP_FILE_SUFFIX = (".pyc",)
SKIP_FILE_NAMES = {"ztq.db", "ztq_bank.db", "index.json"}

# 骨架版保留的 index.json 字段（其余交给用户的导入脚本填）
EMPTY_INDEX = {
    "counts": {"question": 0, "site": 0, "old": 0, "mock": 0, "gradeable": 0,
               "with_answer": 0, "local_answer": 0, "kp_covered": 0,
               "kp": 0, "paper": 0},
    "groups": {},
    "papers": [],
    "mock_papers": [],
    "old_papers": [],
    "knowledge_points": [],
    "questions": [],
    "answer_sources": {},
    "meta": {"skeleton": True, "note": "空框架：尚未导入任何题目"},
}

CREATE_SQL = """
CREATE TABLE question(
  id TEXT PRIMARY KEY,
  group_id TEXT, group_name TEXT,
  year TEXT, paper_name TEXT, paper_id INTEGER, number TEXT,
  qtype INT, qtype_name TEXT, score INT,
  answer TEXT, gradeable INT, answer_letters TEXT,
  kp_name TEXT, kp_id INTEGER, kp_lv1 TEXT, kp_lv2 TEXT,
  img_local TEXT, img_url TEXT,
  diff_n INT, happy INT, maybe INT, sad INT,
  mastery REAL, easy_ratio REAL,
  answer_src TEXT DEFAULT 'site',
  answer_note TEXT DEFAULT '',
  paper_set TEXT DEFAULT '',
  stem TEXT DEFAULT ''
);
CREATE TABLE record(id INTEGER PRIMARY KEY AUTOINCREMENT,
  qid TEXT, ts REAL, action TEXT, value TEXT, ok INT, note TEXT,
  elapsed REAL, last_wrong REAL);
CREATE INDEX ix_rec_qid ON record(qid);
CREATE INDEX ix_rec_ts ON record(ts);
CREATE TABLE srs(qid TEXT PRIMARY KEY, seen INT DEFAULT 0, right_cnt INT DEFAULT 0,
  wrong_cnt INT DEFAULT 0, streak INT DEFAULT 0, ease REAL DEFAULT 2.5,
  last_ts REAL, due REAL);
CREATE TABLE kp(kp_id INTEGER, kp_name TEXT, parent_id INTEGER, level INTEGER, sort INTEGER);
CREATE TABLE paper(paper_id INTEGER, group_id INTEGER, year TEXT, name TEXT, question_cnt INTEGER);
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX ix_q_group ON question(group_id, year);
CREATE INDEX ix_q_kp ON question(kp_name);
CREATE INDEX ix_q_ans ON question(gradeable);
"""


def copytree_filtered(src, dst):
    """复制目录树，跳过数据类目录与中间产物。"""
    kept = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        rel = os.path.relpath(root, src)
        outdir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(outdir, exist_ok=True)
        for f in files:
            if f in SKIP_FILE_NAMES or f.endswith(SKIP_FILE_SUFFIX):
                continue
            if f.startswith("index.html.bak") or f == "_skin_v2.html":
                continue
            shutil.copy2(os.path.join(root, f), os.path.join(outdir, f))
            kept += 1
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=r"D:\try\刷题框架")
    ap.add_argument("--with-capture", action="store_true",
                    help="保留 capture/ 抓包证据（默认不保留）")
    ap.add_argument("--force", action="store_true", help="目标已存在时先删除")
    a = ap.parse_args()

    dest = os.path.abspath(a.dest)
    if os.path.exists(dest):
        if not a.force:
            print(f"[ERR] 目标已存在: {dest}\n      加 --force 覆盖，或换 --dest。")
            return 1
        shutil.rmtree(dest)
    os.makedirs(dest)

    print("=" * 62)
    print(f"生成干净框架版 -> {dest}")
    print("=" * 62)

    # ---- 1. app/（代码 + 空 index.json + KaTeX），剔除 index.json 旧内容
    print("\n[1/5] 复制 app/（代码 + KaTeX，不含 index.json 内容）")
    n = copytree_filtered(APP, os.path.join(dest, "app"))
    with open(os.path.join(dest, "app", "index.json"), "w", encoding="utf-8") as f:
        json.dump(EMPTY_INDEX, f, ensure_ascii=False, separators=(",", ":"))
    print(f"    {n} 个文件 + 空 index.json")

    # ---- 2. tools/（全部脚本，剔除 _build/_archive 与 pyc）
    print("\n[2/5] 复制 tools/（全部脚本）")
    n = copytree_filtered(TOOLS, os.path.join(dest, "tools"))
    # 骨架版需要空 _build 目录占位
    os.makedirs(os.path.join(dest, "tools", "_build"), exist_ok=True)
    print(f"    {n} 个文件 + 空 _build/")

    # ---- 3. 空数据库（只有表结构）
    print("\n[3/5] 生成空数据库（仅表结构）")
    os.makedirs(os.path.join(dest, "data"), exist_ok=True)
    for name in ("ztq.db", "ztq_bank.db"):
        p = os.path.join(dest, "data", name)
        c = sqlite3.connect(p)
        c.executescript(CREATE_SQL)
        c.execute("INSERT INTO meta VALUES('built_at',?)", (str(0),))
        c.execute("INSERT INTO meta VALUES('source',?)",
                  ("https://zhentiqiang.com/kaoyan/math",))
        c.execute("INSERT INTO meta VALUES('group_names','{}')")
        c.commit()
        c.close()
        print(f"    data/{name}  ({os.path.getsize(p)} B, 0 题)")

    # 空目录占位
    for d in ("data/images", "data/mock", "docs", "tools/_build"):
        os.makedirs(os.path.join(dest, d), exist_ok=True)
    print("    data/images/、data/mock/、docs/ 空目录已建")

    # ---- 4. capture/（可选）
    print("\n[4/5] capture/")
    if a.with_capture:
        n = copytree_filtered(CAPTURE, os.path.join(dest, "capture"))
        print(f"    保留抓包证据 {n} 个文件")
    else:
        os.makedirs(os.path.join(dest, "capture"), exist_ok=True)
        print("    跳过（默认不含抓到的站点源码与样本图）")

    # ---- 5. 根文件
    print("\n[5/5] 根目录文件")
    for f in ("启动题库.bat",):
        s = os.path.join(PROJ, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(dest, f))
            print(f"    {f}")

    print("\n完成。接下来请把 README 模板替换进去（见 tools/make_skeleton.py 的说明）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
