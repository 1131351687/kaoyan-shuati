#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
llm_difficulty.py —— 用 LLM 给题目打难度分（替代「先验公式」）

背景：本地老卷（old*）与模拟卷（mock*）没有站点互动数据，原先用结构先验
（1 - 题型先验 + 年份修正 + 分值修正）估计难度。问题是三个题型的先验值
（选择 0.45 / 填空 0.55 / 解答 0.68）全部落在 mid 区间，
导致 **599 道本地题永远进不了 hard 档**。

本脚本直连 LLM 逐题评估难度，把结果写入 question 表的
`llm_diff` / `llm_level` / `llm_reason` / `llm_ts` 四列，供组卷使用。

通道实测（2026-10）：
  ✅ deepseek      api.deepseek.com      deepseek-chat   0.6s、稳定、无配额限制（默认）
  △  free          freeshare.cc.cd       deepseek-v4.1-flash  上游常 no_available_workers
  ✗  cc            api.codex666ai.com    502 upstream_error

用法：
    python tools/llm_difficulty.py --sample 20            # 抽样试跑（不写库）
    python tools/llm_difficulty.py --limit 50 --write     # 评估 50 题并写库
    python tools/llm_difficulty.py --write                # 全量 599 道本地题
    python tools/llm_difficulty.py --export _build/llm_diff.json   # 导出结果

设计要点：
- 分批送入 LLM（默认 10 题一批），要求只回 JSON，解析容错。
- 结果落库前做范围校验（0..1），越界丢弃。
- 幂等：已有 llm_diff 的题默认跳过，`--reeval` 可强制重评。
- 断点续跑：每批写库一次，中断后重跑只处理未评的题。
- 可用 LLM_BASE_URL / LLM_MODEL / LLM_KEY_ENV 环境变量覆盖通道。
"""
import argparse
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import BANK_DB, PROJ, BUILD, PYTHON, ensure_dirs  # noqa: E402

DEFAULT_BATCH = 10

# ── LLM 通道配置 ─────────────────────────────────────────────────────
# 实测（2026-10）：
#   ✅ deepseek      api.deepseek.com       deepseek-chat       0.6s、稳定、无配额（默认）
#   ✅ agnes         apihub.agnes-ai.com    agnes-3.0-flash     真视觉，但 ~12–21s/次，较慢
#   △  free          freeshare.cc.cd        deepseek-v4.1-flash 上游常 no_available_workers
#   ✗  cc            api.codex666ai.com     502 upstream_error
#   ✗  chatanywhere  api.chatanywhere.tech  100 次/24h 免费配额，易耗尽
#
# 本脚本只发**纯文本**（题干在 question.stem，已 100% 补全），不发图片。
#
# 选通道有两种方式：
#   1) 命令行：--channel agnes
#   2) 环境变量：LLM_BASE_URL=... LLM_MODEL=... LLM_KEY_ENV=...
#
# agnes vs deepseek 实测一致性（2026-10，60 道随机本地题，同题同提示词）：
#   Pearson r = 0.874   平均绝对差 0.085   档位一致 47/60 = 78%
#   ⚠️ 但这个"一致性"是单次对单次的假象 —— 真正决定可用性的是**可重复性**：
#     同题同提示词重复 6 次（temperature=0）：
#       deepseek  平均极差 0.026  最大 0.080  档位完全稳定 8/8   → 可信
#       agnes     平均极差 0.220  最大 0.320  档位完全稳定 1/8   → 单次不可信
#     agnes 的单次抖动（0.22）比档位宽度（0.32）还接近，单次结果无意义。
#   ✅ 补救：agnes **重复 N 次取中位数**后可用 —— 中位误差降到 0.00–0.15（均值 0.054），
#      与 deepseek 档位吻合。代价是 N 倍耗时（agnes ~16s/次 vs deepseek ~2s/次）。
#     用法：--channel agnes --repeat 3        （脚本内置中位数聚合）
#   注意：早期算出的"agnes 偏保守 −0.052"是噪声平均出来的伪影，不要据此做偏移校准。
CHANNELS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "agnes":    ("https://apihub.agnes-ai.com/v1", "agnes-3.0-flash", "AGNES_API_KEY"),
    "free":     ("https://freeshare.cc.cd/v1", "deepseek-v4.1-flash", "FREE_API_KEY"),
    "cc":       ("https://api.codex666ai.com/v1", "deepseek-v4.1-flash", "CC_API_KEY"),
    "chatanywhere": ("https://api.chatanywhere.tech/v1",
                     "deepseek-v4.1-flash", "CHATANYWHERE_API_KEY"),
}
DEFAULT_CHANNEL = os.environ.get("LLM_CHANNEL", "deepseek")

_CH = CHANNELS.get(DEFAULT_CHANNEL, CHANNELS["deepseek"])
FREE_BASE = os.environ.get("LLM_BASE_URL", os.environ.get("FREE_BASE_URL", _CH[0]))
FREE_MODEL = os.environ.get("LLM_MODEL", os.environ.get("FREE_MODEL", _CH[1]))
LLM_KEY_ENV = os.environ.get("LLM_KEY_ENV", _CH[2])


def use_channel(name):
    """切换通道（供 --channel 与交叉验证使用）。"""
    global FREE_BASE, FREE_MODEL, LLM_KEY_ENV
    if name not in CHANNELS:
        raise SystemExit("未知通道 %s（可选：%s）" % (name, " ".join(CHANNELS)))
    FREE_BASE, FREE_MODEL, LLM_KEY_ENV = CHANNELS[name]


def mean2(d, keys):
    return sum(d[k]["diff"] for k in keys) / max(1, len(keys))
CRED_YAML = os.path.join(os.path.expanduser(os.environ.get("DSH_HOME", "~/.dsh")),
                         ".credentials.yaml")

# 难度分档（与前端 qLevel 保持一致）
BANDS = ((0.38, "easy"), (0.70, "mid"), (1.01, "hard"))


def band_of(h):
    for hi, name in BANDS:
        if h < hi:
            return name
    return "hard"


PROMPT_SYS = """你是考研数学（数学一，含高等数学、线性代数、概率论与数理统计）难度评估专家。
给你若干道题，逐题评估难度。

难度定义：以「普通考研学生（非顶尖、非薄弱）能否独立做出」为准，输出 0.00–1.00：
- 0.00–0.30 简单（基础题，直接套公式/一眼看出方法）
- 0.30–0.55 较易（常规题，需一两步推理）
- 0.55–0.75 中等（需综合两个以上知识点，或较绕的变形）
- 0.75–0.90 较难（技巧性强、易错、需洞察）
- 0.90–1.00 很难（压轴题/竞赛级，多数学生做不出）

评估时请综合考虑：所需知识点的深度、推理步数、是否有现成模板、计算量、易错程度。
不要因为「题目长」就判难，也不要因为「题目短」就判易。

严格只输出一个 JSON 数组，不要任何解释、不要 markdown 代码围栏。格式：
[{"id":"<原样返回的 id>","difficulty":0.42,"level":"较易","reason":"不超过30字的理由"}]"""


def build_prompt(rows):
    lines = [PROMPT_SYS, "", "===== 待评估题目 =====", ""]
    for r in rows:
        stem = (r["stem"] or "").strip()
        if len(stem) > 900:
            stem = stem[:900] + "…（题干过长已截断）"
        lines.append("[%s] 题型：%s  年份：%s  分值：%s" % (
            r["qid"], r["qtype_name"] or "?", r["year"] or "?", r["score"] or "?"))
        if not stem:
            # 站点题无文字题干，退化为「知识点 + 年份 + 题型」提示
            stem = "（无文字题干，请按以下信息推断）知识点：%s" % (r["kp_name"] or "未分类")
        lines.append(stem)
        lines.append("")
    lines.append("===== 输出 =====")
    lines.append("只输出 JSON 数组，id 必须原样返回。")
    return "\n".join(lines)


def _free_key():
    """取 API Key。支持 LLM_KEY_ENV 指定环境变量名，默认 DEEPSEEK_API_KEY。

    历史：早期用 free 通道（FREE_API_KEY + freeshare.cc.cd），
    实测该通道会 RemoteDisconnected / no_available_workers，已降级为备用。
    """
    return _key_for(LLM_KEY_ENV)


def _key_for(env_name):
    k = os.environ.get(env_name)
    if k:
        return k.strip()
    try:
        with open(CRED_YAML, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(env_name + ":"):
                    return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    raise RuntimeError("找不到 %s（检查环境变量或 %s）" % (env_name, CRED_YAML))


def call_llm(prompt, timeout=300, retries=3):
    """直连 free 通道（OpenAI 兼容 /chat/completions）获取难度评分。

    注意：不要走 dsh subagent 的 provider=free 路由 —— 那条路径会失败退出。
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    key = _free_key()
    body = json.dumps({
        "model": FREE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 4096,
    }).encode("utf-8")

    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            FREE_BASE.rstrip("/") + "/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8"))
            return d["choices"][0]["message"]["content"] or ""
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError("LLM 调用失败：%s: %s" % (type(last).__name__, last))


def parse_scores(text):
    """从 LLM 回复里抽出 [{id,difficulty,...}]，容忍代码围栏与前后噪声。"""
    if not text:
        return {}
    t = text.strip()
    # 去掉 markdown 代码围栏
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # 优先直接解析；失败则用括号配平截取第一个数组
    data = None
    try:
        data = json.loads(t)
    except Exception:
        i = t.find("[")
        if i >= 0:
            depth = 0
            for j in range(i, len(t)):
                if t[j] == "[":
                    depth += 1
                elif t[j] == "]":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(t[i:j + 1])
                        except Exception:
                            pass
                        break
    if not isinstance(data, list):
        return {}
    out = {}
    for it in data:
        if not isinstance(it, dict):
            continue
        qid = str(it.get("id", "")).strip()
        try:
            d = float(it.get("difficulty"))
        except (TypeError, ValueError):
            continue
        if not qid or not (0.0 <= d <= 1.0):
            continue
        out[qid] = {"diff": round(d, 2),
                    "level": band_of(d),
                    "reason": str(it.get("reason", ""))[:60]}
    return out


def apply_shift(scored, shift):
    """把分数整体平移并夹到 [0,1]，同时按新分重定档。"""
    if not shift:
        return scored
    for v in scored.values():
        d = max(0.0, min(1.0, v["diff"] + shift))
        v["diff"] = round(d, 2)
        v["level"] = band_of(d)
    return scored


def median_merge(runs):
    """把同一批题的多次评分按题取中位数（agnes 单次抖动大，靠这个稳住）。

    runs: [{qid: {"diff","level","reason"}}, ...]
    返回与单次同结构的 {qid: {...}}。
    """
    runs = [r for r in runs if r]
    if not runs:
        return {}
    if len(runs) == 1:
        return runs[0]
    out = {}
    for qid in runs[0]:
        vals = sorted(r[qid]["diff"] for r in runs if qid in r)
        if not vals:
            continue
        med = vals[len(vals) // 2]
        # 理由取与中位数最接近的那一次
        best = min((r[qid] for r in runs if qid in r),
                   key=lambda v: abs(v["diff"] - med))
        out[qid] = {"diff": round(med, 2), "level": band_of(med),
                    "reason": best["reason"]}
    return out


def ensure_columns(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    for name, decl in (("llm_diff", "REAL"), ("llm_level", "TEXT"),
                       ("llm_reason", "TEXT"), ("llm_ts", "TEXT")):
        if name not in cols:
            c.execute("ALTER TABLE question ADD COLUMN %s %s" % (name, decl))
    c.commit()


def fetch_pending(c, args):
    """取待评估的本地题（old* / mock*）。"""
    where = ["(group_id LIKE 'old%' OR group_id LIKE 'mock%')"]
    if not args.reeval:
        where.append("llm_diff IS NULL")
    if args.groups:
        gs = "','".join(args.groups)
        where.append("group_id IN ('%s')" % gs)
    if args.types:
        ts = "','".join(args.types)
        where.append("qtype_name IN ('%s')" % ts)
    sql = ("SELECT id AS qid, qtype_name, year, score, kp_name, stem, llm_diff, group_id "
           "FROM question WHERE " + " AND ".join(where) +
           " ORDER BY (group_id LIKE 'mock%'), CAST(substr(group_id,4) AS INT), "
           "CAST(number AS INT)")
    rows = [dict(r) for r in c.execute(sql)]
    if args.limit:
        rows = rows[:args.limit]
    return rows


def main():
    ap = argparse.ArgumentParser(description="LLM 难度评估")
    ap.add_argument("--limit", type=int, help="最多评估多少题")
    ap.add_argument("--sample", type=int, help="随机抽样 N 题试跑（不写库）")
    ap.add_argument("--groups", nargs="*", help="限定分组，如 old2005 mock2024")
    ap.add_argument("--types", nargs="*", help="限定题型：选择题 填空题 解答题")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="每批送 LLM 的题数")
    ap.add_argument("--write", action="store_true", help="写回数据库")
    ap.add_argument("--reeval", action="store_true", help="重评已评估过的题")
    ap.add_argument("--export", help="把结果导出为 JSON 文件")
    ap.add_argument("--prompt-only", action="store_true",
                    help="只打印第一批的 prompt（不调用 LLM，用于人工检查提示词）")
    ap.add_argument("--channel", default=DEFAULT_CHANNEL,
                    help="LLM 通道：%s（默认 %s）" % (" ".join(CHANNELS), DEFAULT_CHANNEL))
    ap.add_argument("--cross-check", metavar="CH2",
                    help="交叉验证：同一批题再用另一个通道评一遍，只比对不写库")
    ap.add_argument("--shift", type=float, default=0.0, metavar="D",
                    help="校准偏移：把所有分数加 D 后夹到 [0,1] 再定档"
                         "（agnes 实测偏保守 −0.052，可用 --shift 0.05 补偿）")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="每批重复评 N 次取中位数。agnes 单次极差可达 0.32（不可信），"
                         "重复 3 次取中位数后与 deepseek 档位吻合；deepseek 本身稳定，"
                         "N=1 即可")
    args = ap.parse_args()

    use_channel(args.channel)
    print("通道：%s  %s  %s" % (args.channel, FREE_BASE, FREE_MODEL))
    if args.repeat > 1:
        print("重复评分：每批 %d 次取中位数（耗时约 %d 倍）" % (args.repeat, args.repeat))
    if args.shift:
        print("校准偏移：%+.3f" % args.shift)

    ensure_dirs()
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row
    ensure_columns(c)

    rows = fetch_pending(c, args)
    if args.sample:
        import random
        random.seed(42)
        # 抽样要从**全部**本地题里取，而不是只从未评过的里面取
        # （否则题库评完之后 --sample 只能抽到零星几道）
        pool = fetch_pending(c, argparse.Namespace(
            reeval=True, groups=args.groups, types=args.types, limit=0))
        rows = random.sample(pool, min(args.sample, len(pool)))
        args.write = False   # 抽样模式强制不写库
    if not rows:
        print("没有待评估的题目（用 --reeval 强制重评）。")
        return
    print("待评估 %d 题，每批 %d 题，共 %d 批" % (
        len(rows), args.batch, (len(rows) + args.batch - 1) // args.batch))

    if args.prompt_only:
        print("\n" + "=" * 70)
        print(build_prompt(rows[:args.batch]))
        return

    results = {}
    t0 = time.time()
    for i in range(0, len(rows), args.batch):
        chunk = rows[i:i + args.batch]
        n = i // args.batch + 1
        total = (len(rows) + args.batch - 1) // args.batch
        try:
            rep = []
            for k in range(max(1, args.repeat)):
                rep.append(parse_scores(call_llm(build_prompt(chunk))))
            got = median_merge(rep)
        except Exception as e:
            print("  批次 %d/%d 失败：%s" % (n, total, e))
            continue
        apply_shift(got, args.shift)

        miss = [r["qid"] for r in chunk if str(r["qid"]) not in got]
        print("  批次 %2d/%d  取回 %2d/%d  %s" % (
            n, total, len(got), len(chunk),
            ("缺 " + ",".join(miss[:5])) if miss else ""))
        results.update(got)

        if args.write and got:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            for qid, v in got.items():
                c.execute("UPDATE question SET llm_diff=?, llm_level=?,"
                          " llm_reason=?, llm_ts=? WHERE id=?",
                          (v["diff"], v["level"], v["reason"], now, qid))
            c.commit()

    dt = time.time() - t0
    print("\n完成：%d/%d 题，耗时 %.0f 秒（%.1f 秒/题）" % (
        len(results), len(rows), dt, dt / max(1, len(rows))))

    if results:
        dist = {}
        for v in results.values():
            dist[v["level"]] = dist.get(v["level"], 0) + 1
        print("难度分布：%s" % dist)

    # 交叉验证：同一批题换通道再评一次，比对一致性（不写库）
    if args.cross_check:
        print("\n" + "=" * 70)
        print("交叉验证：%s vs %s" % (args.channel, args.cross_check))
        use_channel(args.cross_check)
        ct0 = time.time()
        other = {}
        for i in range(0, len(rows), args.batch):
            chunk = rows[i:i + args.batch]
            try:
                other.update(parse_scores(call_llm(build_prompt(chunk))))
            except Exception as e:
                print("  批次 %d 失败：%s" % (i // args.batch + 1, e))
        cdt = time.time() - ct0

        both = [k for k in results if k in other]
        if both:
            diffs = [abs(results[k]["diff"] - other[k]["diff"]) for k in both]
            mean = sum(diffs) / len(diffs)
            same = sum(1 for k in both if results[k]["level"] == other[k]["level"])
            print("  可比 %d 题，耗时 %.0f 秒" % (len(both), cdt))
            print("  平均绝对差 %.3f，最大 %.3f" % (mean, max(diffs)))
            print("  档位一致 %d/%d = %.0f%%" % (same, len(both), 100.0 * same / len(both)))
            ma, mb = mean2(results, both), mean2(other, both)
            cc = sum((results[k]["diff"] - ma) * (other[k]["diff"] - mb) for k in both)
            import math
            sa = math.sqrt(sum((results[k]["diff"] - ma) ** 2 for k in both))
            sb = math.sqrt(sum((other[k]["diff"] - mb) ** 2 for k in both))
            if sa and sb:
                print("  Pearson r = %.3f" % (cc / (sa * sb)))
            print("\n  %-14s %6s %6s %6s" % ("id", args.channel, args.cross_check, "差"))
            for k in sorted(both, key=lambda x: -abs(results[x]["diff"] - other[x]["diff"]))[:8]:
                print("  %-14s %6.2f %6.2f %6.2f  %s→%s" % (
                    k, results[k]["diff"], other[k]["diff"],
                    abs(results[k]["diff"] - other[k]["diff"]),
                    results[k]["level"], other[k]["level"]))
        else:
            print("  无可比对结果")
        return

    if args.export:
        ensure_dirs()
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("已导出 %s" % args.export)

    if not args.write:
        print("\n（未写库；加 --write 才会写回 question.llm_diff）")


if __name__ == "__main__":
    main()
