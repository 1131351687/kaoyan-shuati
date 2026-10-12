# -*- coding: utf-8 -*-
r"""drop_groups.py —— 从题库里彻底删除指定的 group（默认数学二/三：9、10）。

用户只考数学一，故删掉 group 9（数学二）与 group 10（数学三）。

处理范围（三处都要动，否则前端仍会显示）：
  1. data/ztq_bank.db  question / record / srs / kp 里的 9、10
  2. data/ztq.db       站点原始库里的 9、10
  3. data/images/group_9、group_10  题图目录（34.1 MB）
  4. data/mock/        模拟卷与本题无关，不动

然后重新导出 app/index.json。

用法：
    python tools/drop_groups.py --dry-run      # 只看会删什么
    python tools/drop_groups.py                # 真删
    python tools/drop_groups.py --groups 9 10
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB, ZTQ_DB, IMAGES, TOOLS, PYTHON   # noqa: E402


def stats(db, groups):
    if not os.path.exists(db):
        return None
    c = sqlite3.connect(db)
    out = {}
    for g in groups:
        n = c.execute("SELECT COUNT(*) FROM question WHERE group_id=?", (g,)).fetchone()[0]
        out[g] = n
    tot = c.execute("SELECT COUNT(*) FROM question").fetchone()[0]
    c.close()
    return out, tot


def drop(db, groups, dry):
    c = sqlite3.connect(db)
    # 先清子表（record/srs 引用 qid），再清题目
    qids = []
    for g in groups:
        qids += [r[0] for r in c.execute("SELECT id FROM question WHERE group_id=?", (g,))]
    print(f"    {os.path.basename(db)}: 命中 {len(qids)} 题")
    if dry:
        c.close()
        return 0
    for tbl in ("record", "srs"):
        try:
            c.executemany(f"DELETE FROM {tbl} WHERE qid=?", [(q,) for q in qids])
        except sqlite3.OperationalError:
            pass          # 该库可能没有这张表
    n = 0
    for g in groups:
        n += c.execute("DELETE FROM question WHERE group_id=?", (g,)).rowcount
    # 试卷元数据（paper 表）也要清，否则 index.json 的 papers 里会残留
    # 已删分组的空卷（前端下拉里每份卷仍会出现）。
    for tbl in ("paper", "kp"):
        try:
            c.executemany(f"DELETE FROM {tbl} WHERE group_id=?", [(g,) for g in groups])
        except sqlite3.OperationalError:
            pass          # kp 表没有 group_id 列，跳过
    # meta.group_names 里也要去掉，避免下拉出现空分组
    try:
        row = c.execute("SELECT v FROM meta WHERE k='group_names'").fetchone()
        if row:
            names = json.loads(row[0])
            for g in groups:
                names.pop(str(g), None)
            c.execute("UPDATE meta SET v=? WHERE k='group_names'",
                      (json.dumps(names, ensure_ascii=False),))
    except sqlite3.OperationalError:
        pass
    c.commit()          # 必须先提交，VACUUM 不能跑在事务里
    c.execute("VACUUM")
    c.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="+", default=["9", "10"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-images", action="store_true",
                    help="保留 data/images/group_N 目录")
    a = ap.parse_args()

    print("=" * 62)
    print(f"删除 group: {', '.join(a.groups)}" + ("   [DRY-RUN]" if a.dry_run else ""))
    print("=" * 62)

    print("\n[1/4] 删除前统计")
    for lbl, db in (("bank", BANK_DB), ("ztq", ZTQ_DB)):
        s = stats(db, a.groups)
        if s:
            per, tot = s
            print(f"    {lbl}: 共 {tot} 题，其中 " +
                  "、".join(f"group {g}={n}" for g, n in per.items()))

    print("\n[2/4] 从数据库删除")
    total = 0
    for db in (BANK_DB, ZTQ_DB):
        total += drop(db, a.groups, a.dry_run)

    print("\n[3/4] 删除题图目录")
    for g in a.groups:
        d = os.path.join(IMAGES, f"group_{g}")
        if not os.path.isdir(d):
            print(f"    {d} 不存在，跳过")
            continue
        n = sum(len(f) for _, _, f in os.walk(d))
        b = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(d) for f in fs)
        if a.dry_run or a.no_images:
            print(f"    保留 {d}（{n} 张 / {b/1e6:.1f} MB）")
        else:
            shutil.rmtree(d)
            print(f"    已删 {d}（{n} 张 / {b/1e6:.1f} MB）")

    print("\n[4/4] 重新导出 index.json")
    if a.dry_run:
        print("    dry-run，跳过")
        return 0
    import subprocess
    r = subprocess.run([PYTHON, os.path.join(TOOLS, "export_index.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    print("    " + (r.stdout or "").strip().replace("\n", "\n    "))
    if r.returncode != 0:
        print("    [ERR] export_index 失败:\n" + (r.stderr or "")[-600:])
        return 1

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
