# -*- coding: utf-8 -*-
"""从 ztq_bank.db 重新导出 index.json（在答案回填与模拟卷导入之后运行）。

与 build_bank.py 末尾的导出逻辑一致，但独立成步，避免重复建库。
"""
import json
import os
import sqlite3
import time
from paths import APP, BANK_DB, TOOLS

BANK = APP          # index.json 落在 app/ 下
DB = BANK_DB        # 数据库在 data/ 下

# 站点真题分组的显示名。哪几个分组真正出现在下拉里，由数据库里是否还有
# 这些题目决定（见下方 GROUPS 的填充），不在这里写死 —— 否则删掉某个分组
# 后下拉仍会残留一个空选项。
GROUP_NAMES = {"8": "数学一", "9": "数学二", "10": "数学三"}

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

# 只收录库里真还有题目的站点分组
GROUPS = {}
for r in c.execute("SELECT DISTINCT group_id FROM question"
                   " WHERE group_id IN ('8','9','10')"):
    GROUPS[r["group_id"]] = GROUP_NAMES.get(r["group_id"], r["group_id"])

# 老真题组（1987–2008，站点未收录，本地导入）动态并入下拉，
# 名字形如「历年真题（数学一）· 本地」，下拉里显示为「📜 1987（本地）」
OLD = {}
for r in c.execute("SELECT DISTINCT group_id, year FROM question"
                   " WHERE group_id LIKE 'old%' ORDER BY CAST(year AS INT)"):
    OLD[r["group_id"]] = f"{r['year']}（本地）"
for k, v in OLD.items():
    GROUPS[k] = v

MOCK = {}
for r in c.execute("SELECT DISTINCT group_id, year FROM question"
                   " WHERE group_id LIKE 'mock%' ORDER BY year"):
    MOCK[r["group_id"]] = f"模拟卷 {r['year']}"

items = []
for r in c.execute("""SELECT id,group_id,group_name,year,paper_name,number,qtype_name,
                             score,answer,gradeable,kp_name,kp_lv1,kp_lv2,img_local,
                             diff_n,happy,maybe,sad,mastery,
                             answer_src,answer_note,paper_set,stem,
                             llm_diff,llm_level,llm_reason,answer_flag
                      FROM question
                      ORDER BY (group_id LIKE 'mock%'), CAST(group_id AS INT),
                               CAST(year AS INT) DESC, CAST(number AS INT)"""):
    items.append({
        "id": r["id"], "group": r["group_id"], "group_name": r["group_name"],
        "year": r["year"], "paper": r["paper_name"], "num": r["number"],
        "type": r["qtype_name"], "score": r["score"],
        "answer": r["answer"] if r["answer"] else None,
        "gradeable": bool(r["gradeable"]),
        "kp": r["kp_name"], "kp1": r["kp_lv1"], "kp2": r["kp_lv2"],
        "img": (r["img_local"] or "").replace(os.sep, "/"),
        "stem": r["stem"] or "",
        "stats": {"n": r["diff_n"], "happy": r["happy"], "maybe": r["maybe"],
                  "sad": r["sad"], "mastery": r["mastery"]},
        "src": r["answer_src"] or "site", "note": r["answer_note"] or "",
        # 答案质量标注：'doubt' = LLM 认为该答案可能是中间推导（界面提示核对）
        "flag": r["answer_flag"] or "",
        "set": r["paper_set"] or "",
        # LLM 难度评估（只覆盖本地 old*/mock*，站点题用真实 mastery，见 qHardness）
        "llm": (None if r["llm_diff"] is None else
                {"d": r["llm_diff"], "lv": r["llm_level"] or "",
                 "why": r["llm_reason"] or ""}),
    })

kps = [{"id": r[0], "name": r[1], "parent": r[2], "level": r[3]}
       for r in c.execute("SELECT kp_id,kp_name,parent_id,level FROM kp ORDER BY level,sort")]

papers = [{"id": r[0], "group": str(r[1]), "year": r[2], "name": r[3], "count": r[4]}
          for r in c.execute("SELECT paper_id,group_id,year,name,question_cnt FROM paper"
                             " ORDER BY group_id, CAST(year AS INT) DESC")]

mock = [{"group": r[0], "year": r[1], "set": r[2], "count": r[3],
         "total_score": r[4] or 0}
        for r in c.execute("""SELECT group_id,year,paper_set,COUNT(*),SUM(score)
                              FROM question WHERE group_id LIKE 'mock%'
                              GROUP BY group_id,paper_set ORDER BY year DESC, paper_set""")]

# 老真题按年汇总（供前端下拉与统计）
olds = [{"group": r[0], "year": r[1], "count": r[2],
         "with_answer": r[3] or 0}
        for r in c.execute("""SELECT group_id, year, COUNT(*),
                                     SUM(answer<>'')
                                FROM question WHERE group_id LIKE 'old%'
                                GROUP BY group_id,year ORDER BY CAST(year AS INT)""")]

real = [i for i in items if not i["group"].startswith("mock")]
mockq = [i for i in items if i["group"].startswith("mock")]
oldq = [i for i in items if i["group"].startswith("old")]
# 「真题」总数 = 站点 3 组 + 本地老卷
site_real = [i for i in real if not i["group"].startswith("old")]

out = {
    "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "source": "https://zhentiqiang.com/kaoyan/math",
    "answer_sources": {
        "site": "站点 /api/question_info 匿名返回的原生答案（仅选择题 A–D）",
        "local": "本地解析回填（数学一 1987–2025，填空/选择题为主）",
        "local_sol": "本地解析回填的解答题结论句（高可信度）",
        "mock": "模拟卷题干（答案在扫描版 PDF，未数字化）"},
    "counts": {
        "question": len(real),
        "site": len(site_real),
        "old": len(oldq),
        "gradeable": sum(1 for i in real if i["gradeable"]),
        "with_answer": sum(1 for i in real if i["answer"]),
        "local_answer": sum(1 for i in real if i["src"] == "local"),
        "kp_covered": sum(1 for i in items if i["kp"]),
        "mock": len(mockq),
        "kp": len(kps), "paper": len(papers)},
    "groups": GROUPS, "papers": papers,
    "mock_papers": mock, "old_papers": olds,
    "knowledge_points": kps, "questions": items,
}

with open(os.path.join(BANK, "index.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

print(f"index.json: {len(items)} 题"
      f"（真题 {len(real)} + 模拟卷 {len(mockq)}）, "
      f"{os.path.getsize(os.path.join(BANK,'index.json'))/1024/1024:.2f} MB")
print(f"  counts = {out['counts']}")
