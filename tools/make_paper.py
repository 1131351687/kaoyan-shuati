# -*- coding: utf-8 -*-
r"""随机组卷 —— 按难度/知识点/题型随机抽题，生成一份可作答的卷子。

设计要点
--------
1. 难度来源优先级：
   a) 站点难度画像 mastery = (happy+2.5)/(happy+maybe+sad+5)，仅 1224 道站点真题有；
   b) 本地卷（old*/mock*）没有互动数据，用**结构难度**兜底：
      解答题 0.35、填空题 0.45、选择题 0.55（主观难度先验），
      再按该年难度微调（越早的年份略降）。
2. 抽题支持两种模式：
   - `--level` 指定难度档（easy/mid/hard/mixed），mixed 时按比例混合；
   - `--kp` / `--kp1` 限定知识点（支持前缀匹配）；
   - `--types` 限定题型；`--groups` 限定来源组。
3. 生成后可 `--save` 存为 paper 表记录，供 bank.py / 网页按卷做题。

用法
----
    python make_paper.py --n 22 --level mixed --groups 8
    python make_paper.py --n 20 --level hard --kp 二次型 --types 解答题
    python make_paper.py --n 10 --level easy --save "每日一练"
"""
import argparse
import json
import os
import random
import sqlite3
import time
from paths import APP, BANK_DB, TOOLS

BANK = APP          # 导出/引用前端资源时的根
DB = BANK_DB        # 数据库在 data/ 下

# 结构难度先验（本地卷无互动数据时的兜底）
TYPE_PRIOR = {"选择题": 0.55, "填空题": 0.45, "解答题": 0.32}


def local_hardness(r, rng_free=True):
    """本地卷（old*/mock*）的结构难度估计。

    站点题有真实互动数据（mastery），本地卷什么都没有，只能估：
      难度 = 1 - 题型先验 + 年份修正 + 分值修正
    分值修正很关键：解答题里 12 分题通常比 10 分题难，11 分居中。
    这样同是解答题的题也会有 0.62~0.80 的梯度，不会全挤在一个值上。
    """
    h = 1.0 - TYPE_PRIOR.get(r["qtype_name"], 0.5)
    try:
        y = int(r["year"])
        h += max(-0.06, min(0.06, (2012 - y) * 0.005))
    except (TypeError, ValueError):
        pass
    try:
        sc = float(r["score"] or 0)
        # 解答题 10/11/12 分 → -0.03/0/+0.04；填空 4/5 分 → 0/+0.03
        if sc >= 12:
            h += 0.04
        elif sc and sc <= 8:
            h -= 0.03
    except (TypeError, ValueError):
        pass
    return max(0.05, min(0.95, h))
# 难度档 → 难度(hardness) 区间。注意 hardness 越大越难：
#   easy = 站点熟练率高（mastery 高 → hardness 低）
#   hard = 站点熟练率低
# level_of() 必须用同一套阈值，否则 hard 档会抽到 easy 的题。
LEVEL_BANDS = {
    "easy": (0.00, 0.38),
    "mid": (0.38, 0.70),
    "hard": (0.70, 1.01),
}
# mixed 模式下的三档配比
MIX_RATIO = {"easy": 0.3, "mid": 0.4, "hard": 0.3}
# 真题卷面结构（与 app/index.html 的 PAPER_STRUCT、app/bank.py 的 STRUCT 一致）
STRUCT = {"new": {"选择题": 10, "填空题": 6, "解答题": 6},   # 2021 及以后 = 22 题
          "old": {"选择题": 8,  "填空题": 6, "解答题": 9}}   # 2020 及以前 = 23 题
STRUCT_ORDER = ("选择题", "填空题", "解答题")


def load_rows(c, groups=None):
    sql = """SELECT id,group_id,group_name,year,paper_name,number,qtype_name,score,
                    answer,gradeable,kp_name,kp_lv1,kp_lv2,img_local,stem,
                    diff_n,happy,maybe,sad,mastery,answer_src,paper_set,
                    llm_diff,llm_level
               FROM question"""
    args = []
    if groups:
        ph = ",".join("?" * len(groups))
        sql += f" WHERE group_id IN ({ph})"
        args = groups
    return c.execute(sql, args).fetchall()


def hardness(r):
    """归一化难度 0(最易)~1(最难)。

    优先级与 app/index.html 的 qHardness()、app/bank.py 的 cmd_paper 一致：
      1) mastery（站点真实互动数据）
      2) llm_diff（tools/llm_difficulty.py 的 LLM 评估）
      3) 结构先验（兜底，只按题型/年份/分值打分）
    """
    m = r["mastery"]
    if m is not None:
        return 1.0 - float(m)
    try:
        if r["llm_diff"] is not None:
            return max(0.02, min(0.98, float(r["llm_diff"])))
    except (KeyError, IndexError):
        pass
    return local_hardness(r)


def level_of(h):
    """把归一化难度(hardness，越大越难)映射到 easy/mid/hard 档。"""
    if h < 0.38:
        return "easy"
    if h < 0.70:
        return "mid"
    return "hard"


def src_of(r):
    """难度来源标注（与 app/index.html 的 qSrc 一致）。"""
    if r["mastery"] is not None:
        return "站点"
    try:
        if r["llm_diff"] is not None:
            return "LLM"
    except (KeyError, IndexError):
        pass
    return "先验"


def match_kp(r, kps):
    if not kps:
        return True
    name = r["kp_name"] or ""
    if not name:
        return False
    return any(k.lower() in name.lower() for k in kps)


def pick(rows, args, rng):
    """按条件抽题，返回选中的行列表。

    --struct 不为 off 时走「真题结构」：按题型配额抽题，
    最后按 选择→填空→解答 分段、段内由难到易（与真卷卷面一致）。
    """
    pool = [r for r in rows
            if (not args.types or r["qtype_name"] in args.types)
            and match_kp(r, args.kp)
            and (not args.kp1 or (r["kp_lv1"] or "") in args.kp1)]
    if not pool:
        return []

    def by_level(cand, want):
        """在 cand 里挑 want 道：优先满足难度档，不足则放宽到全部 cand 补齐。"""
        cand = list(cand)
        rng.shuffle(cand)
        if args.level == "mixed":
            out = []
            for lv, frac in MIX_RATIO.items():
                c2 = [r for r in cand if level_of(hardness(r)) == lv]
                rng.shuffle(c2)
                out.extend(c2[: round(want * frac)])
            if len(out) < want:
                got = {r["id"] for r in out}
                rest = [r for r in cand if r["id"] not in got]
                rng.shuffle(rest)
                out.extend(rest[: want - len(out)])
            return out[:want]
        lo, hi = LEVEL_BANDS[args.level]
        sub = [r for r in cand if lo <= hardness(r) < hi]
        got = sub[:want]
        if len(got) < want:                # 该档不够 → 放宽到全部同题型题
            ids = {r["id"] for r in got}
            got.extend([r for r in cand if r["id"] not in ids][: want - len(got)])
        return got

    if args.struct != "off":
        sm = args.struct
        if sm == "auto":
            sm = "old" if (args.groups and all(str(g).startswith("old") for g in args.groups)) else "new"
        out, short = [], {}
        for tp in STRUCT_ORDER:
            want = STRUCT[sm].get(tp, 0)
            if not want:
                continue
            cand = [r for r in pool if r["qtype_name"] == tp]
            got = by_level(cand, want)
            if len(got) < want:
                short[tp] = (len(got), want, len(cand))
            out.extend(got)
        if short:
            print("⚠ 以下题型题量不足（题库中该题型总数不够）：")
            for tp, (g, w, n) in short.items():
                print(f"    {tp}: 抽到 {g}/{w} 道（可用 {n} 道）")
        return [r for tp in STRUCT_ORDER
                for r in sorted([x for x in out if x["qtype_name"] == tp],
                                key=hardness, reverse=True)]

    if args.level == "mixed":
        return by_level(pool, args.n)

    lo, hi = LEVEL_BANDS[args.level]
    cand = [r for r in pool if lo <= hardness(r) < hi]
    if not cand:                       # 区间太窄时退化到整体池
        cand = pool
    rng.shuffle(cand)
    return cand[: args.n]


def show(rows, args):
    print(f"\n{'='*66}")
    print(f"随机卷：{len(rows)} 题   难度档={args.level}"
          + (f"   结构={STRUCT[args.struct]}" if args.struct != "off" else "")
          + (f"   知识点={','.join(args.kp)}" if args.kp else "")
          + (f"   题型={','.join(args.types)}" if args.types else ""))
    print('=' * 66)
    total = 0
    for i, r in enumerate(rows, 1):
        h = hardness(r)
        total += r["score"] or 0
        src = src_of(r)
        setname = f"[{r['paper_set']}]" if r["paper_set"] else ""
        print(f"{i:>2}. [{r['qtype_name']} {r['score']}分] {r['group_id']} "
              f"{r['year']}{setname} # {r['number']}  难度{h:.2f}({level_of(h)})[{src}]"
              f"  {r['kp_name'] or '未分类'}")
    print('-' * 66)
    print(f"总分 {total} 分")
    dist = {}
    for r in rows:
        dist[level_of(hardness(r))] = dist.get(level_of(hardness(r)), 0) + 1
    print("难度分布:", dist)


def save(c, rows, name):
    """把生成的卷子存进 paper 表 + 建一张作答记录（供网页按卷作答）。"""
    pid = f"rnd{int(time.time())}"
    gid = "rnd"
    c.execute("""CREATE TABLE IF NOT EXISTS random_paper(
                   paper_id TEXT, name TEXT, created TEXT, seq INTEGER, qid TEXT)""")
    c.execute("DELETE FROM random_paper WHERE paper_id=?", (pid,))
    for i, r in enumerate(rows, 1):
        c.execute("INSERT INTO random_paper VALUES(?,?,?,?,?)",
                  (pid, name, time.strftime("%Y-%m-%d %H:%M:%S"), i, r["id"]))
    c.commit()
    return pid


def main():
    ap = argparse.ArgumentParser(description="随机组卷（可按难度）")
    ap.add_argument("--n", type=int, default=22, help="题量")
    ap.add_argument("--level", default="mixed",
                    choices=["easy", "mid", "hard", "mixed"], help="难度档")
    ap.add_argument("--groups", nargs="*", default=None, help="来源组，如 8 或 old2005")
    ap.add_argument("--kp", nargs="*", default=None, help="知识点关键词（子串匹配）")
    ap.add_argument("--kp1", nargs="*", default=None, help="一级章节，如 高等数学")
    ap.add_argument("--types", nargs="*", default=None,
                    help="题型：选择题 填空题 解答题")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    ap.add_argument("--save", default=None, help="存卷名（写入 random_paper 表）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--struct", default="off", choices=["off", "auto", "new", "old"],
                    help="卷面结构：off=仅按难度 / auto=按范围自动 / "
                         "new=10选+6填+6解 / old=8选+6填+9解")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = load_rows(c, args.groups)
    picked = pick(rows, args, rng)
    if not picked:
        print("没有匹配的题目，请放宽条件。")
        return 1
    if args.json:
        print(json.dumps([{"id": r["id"], "type": r["qtype_name"],
                           "score": r["score"], "year": r["year"],
                           "kp": r["kp_name"], "h": round(hardness(r), 3)}
                          for r in picked], ensure_ascii=False, indent=1))
    else:
        show(picked, args)
    if args.save:
        pid = save(c, picked, args.save)
        print(f"\n已存卷：{args.save}  ({pid})")


if __name__ == "__main__":
    raise SystemExit(main())
