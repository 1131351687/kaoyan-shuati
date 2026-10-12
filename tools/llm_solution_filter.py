# -*- coding: utf-8 -*-
"""用 agnes 判定解答题「结论候选」是否真的是答案（而非中间推导/残片）。

背景：extract_solutions.py 用纯正则抽 `故/因此/所以` 引导的结论，实测仍混入
中间推导（"有 6a/-3 = 8b/-4 即 a=b"）、方法描述（"分别在 x=0 及 x=1 处展开"）、
LaTeX/HTML 残片（"<table>…"、"0 2 2A = − −"）。正则拦不住，交给 LLM 判别。

不调用 deepseek 官方 API —— 走 agnes（apihub.agnes-ai.com）。

用法:
    python tools/llm_solution_filter.py --sample 12          # 抽样看判定质量，不写库
    python tools/llm_solution_filter.py --write              # 全量判定并写库
    python tools/llm_solution_filter.py --write --batch 12
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import SOLUTIONS_RAW, BANK_DB

# ── agnes 通道（不调用 deepseek 官方 API）──
AGNES_BASE = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
AGNES_MODEL = os.environ.get("AGNES_MODEL", "agnes-3.0-flash")
CRED_YAML = os.path.join(os.path.expanduser(os.environ.get("DSH_HOME", "~/.dsh")),
                         ".credentials.yaml")

SYS = """你是考研数学阅卷老师。下面给你若干道解答题的「结论候选」——
它们是从参考解答里用规则抽出来的句子，可能真的是一道题的最终答案，
也可能只是中间推导、方法描述、或文本残缺。

对每一条判断它是否是**该题的最终答案**：
- 是最终答案（能直接作答/判分的结论，如"最大似然估计量为 …"、"积分值为 …"、"矩阵为 …"）
- 不是（中间推导如"即 a=b"、方法描述如"分别展开"、残片如"0 2 2A = − −"、纯表格 HTML）

严格输出 JSON 数组，每项 {"i": 序号, "ok": true/false, "conf": 0~1, "why": "简短理由"}
不要输出任何其他内容、不要用 markdown 围栏。"""


def _key(name):
    v = os.environ.get(name)
    if v:
        return v
    if os.path.exists(CRED_YAML):
        for ln in open(CRED_YAML, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*" + name + r"\s*:\s*(\S+)", ln)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def call_llm(prompt, timeout=300, retries=3):
    key = _key("AGNES_API_KEY")
    if not key:
        raise RuntimeError("缺少 AGNES_API_KEY（环境变量或 .credentials.yaml）")
    body = json.dumps({
        "model": AGNES_MODEL,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 8192,
    }).encode("utf-8")
    req = urllib.request.Request(
        AGNES_BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last = None
    for a in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8"))
            return d["choices"][0]["message"]["content"]
        except Exception as e:                       # noqa: BLE001
            last = e
            if a < retries:
                time.sleep(2 * a)
    raise RuntimeError(f"agnes 调用失败: {last}")


def parse_verdicts(text, n):
    """解析 LLM 的 JSON 数组，返回 {序号: (ok, conf, why)}。

    实测坑：agnes 有时把整个数组**双重编码**成 JSON 字符串返回
    （回复首尾带单引号/双引号，形如 `'[{"i":0,…}]'`），此时直接
    json.loads 会得到 str 而不是 list，必须先 unquote 再解析。
    """
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip()

    def _try_parse(s):
        s = s.strip()
        if s.startswith("'") and s.endswith("'") and len(s) > 1:
            s = '"' + s[1:-1].replace('"', '\\"') + '"'
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    def _salvage(s):
        """抢救被截断的 JSON 数组。

        实测：agnes 偶尔 finish_reason='stop' 但回复缺结尾 `]`
        （completion_tokens 远未达上限，属服务端提前收尾）。
        做法是逐个对象扫描，把能解析的完整对象收下，遇到残缺的就停。
        """
        s = s[s.find("["):] if "[" in s else s
        objs, depth, start, i, n = [], 0, None, s.find("["), len(s)
        while i >= 0 and i < n:
            ch = s[i]
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objs.append(json.loads(s[start:i + 1]))
                    except json.JSONDecodeError:
                        pass
                    start = None
            i += 1
        return objs

    obj = _try_parse(t)
    if isinstance(obj, str):
        obj = _try_parse(obj)
    if isinstance(obj, dict) and "i" in obj:
        obj = [obj]                # ← 单条时 agnes 会返回裸对象而非数组
    if not isinstance(obj, list):
        i, j = t.find("["), t.rfind("]")
        if i >= 0 and j > i:
            obj = _try_parse(t[i:j + 1])
    if not isinstance(obj, list):
        obj = _salvage(t)          # ← 截断抢救
    if not isinstance(obj, list):
        return {}

    out = {}
    for it in obj:
        if not isinstance(it, dict):
            continue
        try:
            k = int(it.get("i"))
            if 0 <= k < n:
                out[k] = (bool(it.get("ok")), float(it.get("conf") or 0),
                          str(it.get("why") or "")[:120])
        except (TypeError, ValueError):
            continue
    return out


def load_candidates(limit=None, sample=None, seed=0):
    """从 _solutions_raw.json 读候选，与库中「无答案的解答题」配对。"""
    raw = json.load(open(SOLUTIONS_RAW, encoding="utf-8"))
    import sqlite3
    c = sqlite3.connect(BANK_DB)
    c.row_factory = sqlite3.Row
    rows = []
    for year, items in raw.items():
        for num, v in items.items():
            got = c.execute(
                "SELECT id,group_id FROM question WHERE group_id='8' AND year=? "
                "AND number=? AND qtype_name='解答题'", (year, num)).fetchone()
            if not got:
                continue
            rows.append({"year": year, "num": num, "qid": got["id"],
                         "ans": v["ans"], "conf": v["conf"]})
    if sample:
        import random
        rng = random.Random(seed)
        rng.shuffle(rows)
        rows = rows[:sample]
    if limit:
        rows = rows[:limit]
    return rows


def build_prompt(batch):
    lines = []
    for i, r in enumerate(batch):
        a = r["ans"]
        if len(a) > 400:
            a = a[:400] + " …"
        lines.append(f'[{i}] 题号 {r["year"]}年#{r["num"]}\n候选：{a}')
    return "\n\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sample", type=int, default=None, help="抽样 N 条，不写库")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--write", action="store_true", help="把判定结果写回库")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = load_candidates(args.limit, args.sample, args.seed)
    if not rows:
        print("没有候选（解答题是空的？）")
        return 1
    print(f"候选 {len(rows)} 条，批大小 {args.batch}"
          + ("（抽样模式，不写库）" if args.sample else ""))

    all_v = {}
    t0 = time.time()
    nb = (len(rows) + args.batch - 1) // args.batch
    for bi in range(nb):
        batch = rows[bi * args.batch:(bi + 1) * args.batch]
        try:
            txt = call_llm(build_prompt(batch))
            v = parse_verdicts(txt, len(batch))
        except Exception as e:                       # noqa: BLE001
            print(f"  批 {bi+1}/{nb} 失败: {str(e)[:120]}")
            continue
        for k, (ok, conf, why) in v.items():
            r = batch[k]
            all_v[(r["year"], r["num"])] = (ok, conf, why, r)
        ng = sum(1 for k, (ok, *_x) in v.items() if ok)
        print(f"  批 {bi+1}/{nb}  取回 {len(v)}/{len(batch)}  判为真答案 {ng}")
    dt = time.time() - t0
    print(f"\n完成 {len(all_v)}/{len(rows)} 条，耗时 {dt:.1f} 秒"
          + (f"（{dt/max(1,len(all_v)):.2f} 秒/条）" if all_v else ""))

    yes = [k for k, v in all_v.items() if v[0]]
    print(f"判定为真答案: {len(yes)} / {len(all_v)}")
    print("\n--- 抽样明细 ---")
    for k in list(all_v)[:12]:
        ok, conf, why, r = all_v[k]
        print(f"  {k[0]}#{k[1]:<3} [{'✓' if ok else '✗'}] conf={conf:.1f} {why[:60]}")
        print(f"        {r['ans'][:88]}")

    if args.sample:
        print("\n（抽样模式，未写库。去掉 --sample 加 --write 才会写。）")
        return 0
    if not args.write:
        print("\n（未加 --write，未写库）")
        return 0

    import sqlite3
    c = sqlite3.connect(BANK_DB)
    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    for col, typ in (("sol_ok", "INTEGER"), ("sol_conf", "REAL"), ("sol_why", "TEXT")):
        if col not in cols:
            c.execute(f"ALTER TABLE question ADD COLUMN {col} {typ}")
    n = 0
    for k, (ok, conf, why, r) in all_v.items():
        c.execute("UPDATE question SET sol_ok=?,sol_conf=?,sol_why=? WHERE id=?",
                  (1 if ok else 0, conf, why, r["qid"]))
        n += 1
    c.commit()
    print(f"\n已写库 {n} 条（question.sol_ok/sol_conf/sol_why）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
