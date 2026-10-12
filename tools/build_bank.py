# -*- coding: utf-8 -*-
"""构建本地题库 ztq_bank.db + 导出 JSON 索引。

在原始 ztq.db 基础上增加：
  - 可移植的本地图片相对路径 img_local
  - 知识点名称/父级（从 raw JSON + kp 表解出）
  - 全站难度画像 difficulty（由 happy/maybe/sad 计数计算）
  - 答案解析 a/b/c/d 分布
输出：
  BANK_DB      本地题库（含全部元数据）
  INDEX_JSON       静态索引（给浏览页用）
"""
import json
import os
import sqlite3
import time
from paths import APP, BANK_DB, IMAGES, INDEX_JSON, TOOLS, ZTQ_DB

SRC = ZTQ_DB
BANK = APP                      # index.json 落在 app/ 下
DST = BANK_DB                   # 数据库在 data/ 下
IMGROOT = IMAGES                # 题图在 data/images/ 下

# 站点真题分组的显示名。只保留你实际要考的分组 —— 想恢复数学二/三，
# 把 "9"/"10" 加回来，或直接用 tools/rebuild_bank.py 全量重建。
GROUP_NAMES = {"8": "数学一", "9": "数学二", "10": "数学三"}
GROUPS = dict(GROUP_NAMES)

if os.path.exists(DST):
    os.remove(DST)
src = sqlite3.connect(SRC)
dst = sqlite3.connect(DST)

dst.executescript("""
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
  -- 答案来源：'site'(站点原生,仅选择题) / 'local'(本地解析回填) / 'mock'(模拟卷,无答案)
  answer_src TEXT DEFAULT 'site',
  answer_note TEXT DEFAULT '',
  -- 模拟卷套号，如 '第1套'；站点真题为空
  paper_set TEXT DEFAULT '',
  -- 题干原文：仅模拟卷有（站点真题的题干以题图形式存储）
  stem TEXT DEFAULT ''
);
CREATE TABLE record(id INTEGER PRIMARY KEY AUTOINCREMENT,
  qid TEXT, ts REAL, action TEXT, value TEXT, ok INT, note TEXT,
  -- 作答耗时（秒）：NULL 表示未计时（老记录/CLI 快速作答）
  elapsed REAL,
  -- 错题本：最近一次答错的时间戳；答对不清空，便于回顾历史错题
  last_wrong REAL);
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
""")

# 复制 kp / paper / meta
for r in src.execute("SELECT kp_id,kp_name,parent_id,level,sort FROM kp"):
    dst.execute("INSERT INTO kp VALUES(?,?,?,?,?)", r)
for r in src.execute("SELECT paper_id,group_id,year,name,question_cnt FROM paper"):
    dst.execute("INSERT INTO paper VALUES(?,?,?,?,?)", r)

# kp_id -> 名称、父链
kp_name = {r[0]: r[1] for r in src.execute("SELECT kp_id,kp_name FROM kp")}
kp_par = {r[0]: r[1] for r in src.execute("SELECT kp_id,parent_id FROM kp")}


def chain(kid):
    """返回 (一级, 二级) 知识点名"""
    lv1 = lv2 = ""
    cur, depth = kid, 0
    while cur is not None and depth < 8:
        nm = kp_name.get(cur, "")
        if depth == 0:
            lv2 = nm
        cur = kp_par.get(cur)
        if cur is not None:
            lv1 = kp_name.get(cur, "")
        depth += 1
    return lv1, lv2


n = 0
for row in src.execute("""SELECT id,group_id,paper_name,year,number,qtype,score,
                                 image_url,full_image,answer,raw
                          FROM question"""):
    (qid, gid, paper, year, num, qtype, score,
     img_url, full_image, answer, rawj) = row

    meta = {}
    if rawj:
        try:
            meta = json.loads(rawj)
        except Exception:
            meta = {}

    kpname = (meta.get("knowledge_point_name") or "").strip()
    kpraw = meta.get("knowledge_tags_id")
    kp_id = None
    if isinstance(kpraw, int):
        kp_id = kpraw
    elif isinstance(kpraw, str) and kpraw.strip().isdigit():
        kp_id = int(kpraw)
    elif isinstance(kpraw, list) and kpraw and str(kpraw[0]).isdigit():
        kp_id = int(kpraw[0])
    # knowledge_tags_id 常为逗号分隔的祖链，取最深（最后）一个
    if isinstance(kpraw, str) and "," in kpraw:
        parts = [p for p in kpraw.split(",") if p.strip().isdigit()]
        if parts:
            kp_id = int(parts[-1])

    lv1 = lv2 = ""
    if kp_id is not None:
        lv1, lv2 = chain(kp_id)
    if not kpname:
        kpname = lv2 or lv1

    ans = (answer or "").strip().upper()
    letters = "".join(sorted(set(ch for ch in ans if ch in "ABCD")))
    gradeable = 1 if letters else 0

    h = int(meta.get("happy_count") or 0)
    m = int(meta.get("maybe_count") or 0)
    s = int(meta.get("sad_count") or 0)
    dn = h + m + s
    mastery = (h / dn) if dn else None          # 全站"熟练"比例
    easy = ((h + m) / dn) if dn else None        # 非"不会"比例
    # 难度画像：以熟练率为基础，样本量做置信收缩（10 次封顶）
    if dn:
        adj = (h + 2.5) / (dn + 5.0)             # Beta(2.5,2.5) 先验
    else:
        adj = None

    qtype_name = {1: "选择题", 2: "填空题", 3: "解答题"}.get(qtype, f"类型{qtype}")
    rel = (full_image or img_url or "").replace("/static/photos/", "").strip("/")
    img_local = rel.replace("/", os.sep) if rel else ""

    dst.execute("""INSERT INTO question VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(qid), str(gid), GROUPS.get(str(gid), str(gid)), str(year), str(paper),
         meta.get("paper_id"), str(num), qtype, qtype_name, score,
         ans, gradeable, letters,
         kpname, kp_id, lv1, lv2,
         img_local, img_url,
         dn, h, m, s,
         round(adj, 4) if adj is not None else None,
         round(easy, 4) if easy is not None else None,
         "site", "", "", ""))
    n += 1

dst.execute("INSERT INTO meta VALUES('built_at',?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
dst.execute("INSERT INTO meta VALUES('source',?)", ("https://zhentiqiang.com/kaoyan/math",))
dst.execute("INSERT INTO meta VALUES('group_names',?)", (json.dumps(GROUPS, ensure_ascii=False),))
dst.execute("INSERT INTO meta VALUES('image_root',?)", ("images",))
dst.commit()

print(f"本地题库已构建: {DST}")
print(f"  题目: {n}")
print(f"  可判分(有答案): {dst.execute('SELECT COUNT(*) FROM question WHERE gradeable=1').fetchone()[0]}")
print(f"  有知识点名: {dst.execute('SELECT COUNT(*) FROM question WHERE kp_name IS NOT NULL AND kp_name<>\"\"').fetchone()[0]}")
print(f"  有难度画像: {dst.execute('SELECT COUNT(*) FROM question WHERE diff_n>0').fetchone()[0]}")
print(f"  知识点: {dst.execute('SELECT COUNT(*) FROM kp').fetchone()[0]}")
print(f"  试卷: {dst.execute('SELECT COUNT(*) FROM paper').fetchone()[0]}")

print("\n=== 最难 10 题（全站熟练率最低，样本>=20）===")
for r in dst.execute("""SELECT id,year,number,kp_name,happy,maybe,sad,mastery
                        FROM question WHERE diff_n>=20
                        ORDER BY mastery ASC LIMIT 10"""):
    print(f"  qid={r[0]:>5} {r[1]}年#{r[2]:<3} {str(r[3])[:22]:24} "
          f"熟练{r[4]:>3}/不熟{r[5]:>3}/不会{r[6]:>4}  熟练率={r[7]*100:5.1f}%")

print("\n=== 最易 10 题 ===")
for r in dst.execute("""SELECT id,year,number,kp_name,happy,maybe,sad,mastery
                        FROM question WHERE diff_n>=20
                        ORDER BY mastery DESC LIMIT 10"""):
    print(f"  qid={r[0]:>5} {r[1]}年#{r[2]:<3} {str(r[3])[:22]:24} "
          f"熟练{r[4]:>3}/不熟{r[5]:>3}/不会{r[6]:>4}  熟练率={r[7]*100:5.1f}%")

print("\n=== 按知识点聚合（题量>=20，按平均熟练率升序 = 最难章节）===")
for r in dst.execute("""SELECT kp_name, COUNT(*) n, AVG(mastery) avg_m, SUM(diff_n) samples
                        FROM question WHERE kp_name<>'' AND diff_n>0
                        GROUP BY kp_name HAVING samples>=50
                        ORDER BY avg_m ASC LIMIT 12"""):
    print(f"  {str(r[0])[:26]:28} 题量{r[1]:3}  平均熟练率={r[2]*100:5.1f}%  样本{r[3]}")

print("\n=== 答案分布 ===")
for r in dst.execute("""SELECT answer, COUNT(*) FROM question WHERE gradeable=1
                        GROUP BY answer ORDER BY COUNT(*) DESC"""):
    print(f"  {r[0]}: {r[1]}")

# ---------- 导出 index.json ----------
items = []
for r in dst.execute("""SELECT id,group_id,group_name,year,paper_name,number,qtype_name,
                               score,answer,gradeable,kp_name,kp_lv1,kp_lv2,img_local,
                               diff_n,happy,maybe,sad,mastery,
                               answer_src,answer_note,paper_set
                        FROM question
                        ORDER BY CAST(group_id AS INT), CAST(year AS INT) DESC,
                                 CAST(number AS INT)"""):
    items.append({
        "id": r[0], "group": r[1], "group_name": r[2], "year": r[3], "paper": r[4],
        "num": r[5], "type": r[6], "score": r[7],
        "answer": r[8] if r[8] else None, "gradeable": bool(r[9]),
        "kp": r[10], "kp1": r[11], "kp2": r[12],
        "img": (r[13] or "").replace(os.sep, "/"),
        "stats": {"n": r[14], "happy": r[15], "maybe": r[16], "sad": r[17],
                  "mastery": r[18]},
        "src": r[19] or "site", "note": r[20] or "", "set": r[21] or "",
    })

kps = [{"id": r[0], "name": r[1], "parent": r[2], "level": r[3]}
       for r in dst.execute("SELECT kp_id,kp_name,parent_id,level FROM kp ORDER BY level,sort")]

papers = [{"id": r[0], "group": str(r[1]), "year": r[2], "name": r[3], "count": r[4]}
          for r in dst.execute("SELECT paper_id,group_id,year,name,question_cnt FROM paper"
                               " ORDER BY group_id, CAST(year AS INT) DESC")]

# ---- 模拟卷清单（group_id 形如 mock2024，独立于站点三组）----
mock = []
for r in dst.execute("""SELECT group_id, year, paper_set, COUNT(*), SUM(score)
                        FROM question WHERE group_id LIKE 'mock%'
                        GROUP BY group_id, paper_set ORDER BY year DESC, paper_set"""):
    mock.append({"group": r[0], "year": r[1], "set": r[2], "count": r[3],
                 "total_score": r[4] or 0})

out = {"built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
       "source": "https://zhentiqiang.com/kaoyan/math",
       "answer_sources": {
           "site": "站点 /api/question_info 匿名返回的原生答案（仅选择题）",
           "local": "本地 2024/2025 解析与 2025 真题回填（含填空题、少量解答题）"},
       "counts": {"question": sum(1 for i in items if not i["group"].startswith("mock")),
                  "gradeable": sum(1 for i in items if i["gradeable"]),
                  "mock": sum(1 for i in items if i["group"].startswith("mock")),
                  "kp": len(kps), "paper": len(papers),
                  "local_answer": sum(1 for i in items if i["src"] == "local")},
       "groups": GROUPS, "papers": papers, "mock_papers": mock,
       "knowledge_points": kps, "questions": items}


with open(os.path.join(BANK, "index.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print(f"\nindex.json 已导出: {len(items)} 题, "
      f"{os.path.getsize(os.path.join(BANK,'index.json'))/1024/1024:.2f} MB")
dst.close()
src.close()
