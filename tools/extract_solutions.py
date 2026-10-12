# -*- coding: utf-8 -*-
"""
抽取【解答题】的最终答案。

背景：真题解析里解答题只写 `（15）【解】 <推导过程>`，没有 `【答案】` 标记，
所以 extract_answers.py 只抓到了选择题和填空题。但解答题的实际结论就在
`【解】` 段落里，通常在末尾（"故 …"、"因此 …"、最后一个公式）。

抽取策略（保守优先，宁可漏也不要错）：
  1. 定位解答题节（`三、解答题` 或 `# 三、解答题`）
  2. 按 `（n）【解】` / `n．解：` 切出每题的解
  3. 在该解里找"收尾结论"：
     - 优先取最后的 `故 <...>` / `因此 <...>` / `所以 <...>` 分句
     - 否则取最后一个独立行公式 $$...$$
     - 都找不到就标记为 '需人工'
  4. 截断过长的结论（>200 字符的只保留首句）

输出：SOLUTIONS_RAW  {year: {题号: {ans, conf, src_lines}}}
"""
import json
import os
import re
from paths import SOLUTIONS_RAW, STUDY_JIEXI, TOOLS

SRC_DIR = STUDY_JIEXI
OUT = SOLUTIONS_RAW

# 解答题节标题
SEC_ESSAY = re.compile(r"^#*\s*三、\s*解答题", re.M)
# 老卷（1987–1999）：三、四、五… 只写中文数字，没有"解答题"字样；
# 最后两节是"填空题/选择题"，所以取「三、」到「填空题/选择题」之间为解答题区。
SEC_CN_ANY = re.compile(r"^#*\s*[一二三四五六七八九十]+、", re.M)
SEC_TAIL = re.compile(r"^#*\s*[一二三四五六七八九十]+、\s*(填空题|选择题)", re.M)
SEC_NEXT = re.compile(r"^#*\s*[四五六]、", re.M)

# 题号 + 【解】 同行：（15）【解】 ...
Q_SOL = re.compile(r"^#*\s*[（(]\s*(\d{1,2})\s*[）)]\s*【解】\s*(.*)$", re.M)
# 2025 风格：17．解：
Q_SOL2 = re.compile(r"^#*\s*(\d{1,2})\s*[．.]\s*解\s*[:：]\s*(.*)$", re.M)
# 老卷风格：一整节就是一道解答题，形如 `# 四、【解】 方法一`
#            或裸 `【解】...`（节标题已隐含题号）
Q_SOL_CN = re.compile(r"^#*\s*([一二三四五六七八九十]+)\s*[、.]?\s*【(?:解|证明)】\s*(.*)$", re.M)
Q_SOL_BARE = re.compile(r"^#*\s*(?:【解】|【证明】)\s*(.*)$", re.M)

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13,
          "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18,
          "十九": 19, "二十": 20}


def strip_md(s):
    s = re.sub(r"\$+", "", s)
    s = re.sub(r"\\[a-zA-Z]+", "", s)      # 去掉 \frac \lim 等命令名
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_conclusion(s):
    """从一句里抽出干净的结论文本。"""
    s = s.strip()
    s = re.sub(r"^[，,。.、;；:：\s]+", "", s)
    s = re.sub(r"[，,。.\s]+$", "", s)
    # 去掉“(1)”“(I)”这类小问标号前缀
    s = re.sub(r"^[（(]\s*(?:[IVX]+|\d+)\s*[）)]\s*", "", s)
    if len(s) > 220:
        s = s[:220] + " …"
    return s


def is_resultlike(s):
    r"""
    判断一句"结论"是否真的像个答案，而不是中间推导。

    实测踩过的坑（都是从真实抽取结果里发现的）：
      - `有 \frac{6a}{-3} = \frac{8b}{-4} 即 a = b`        → 中间推导，非最终结论
      - `分别在 x=0 及 x=1 处使用泰勒公式展开`            → 方法描述，且是证明题
      - `\frac{\partial P}{\partial y} ... \end{array}`   → LaTeX 结构被截断的残片
      - `1 2`                                              → 残片
    因此除「像答案」外还要主动**排除**这些形态。

    必须满足其一：
      - 含数字或简单常量（含 $\frac$、$\sqrt$、$\pi$、$\mathrm{e}$ 等）
      - 是以"略/证毕/得证"结尾的证明结论
      - 是明确的集合/区间/矩阵/函数表达式
    """
    if not s or len(s) < 2:
        return False
    plain = strip_md(s)

    # ── 排斥 1: 方法/推导口吻（不是答案）──
    if re.search(r"方法[一二三]|第[一二三四]步|依次类推|下面(说明|证明)|"
                 r"^(运用|利用|根据|按照|通过|采用|使用|代入|将)|"
                 r"(使用|应用|利用|运用|代入|展开|设|令|取|作|作辅助|"
                 r"分别在|同理|类似|继续|进而|由此|从而|计算得|解得|证明)", plain):
        return False

    # ── 排斥 2: LaTeX 结构残片（截断破坏了对环境）──
    if re.search(r"\\(end|begin)\s*\{(array|matrix|cases|pmatrix|aligned)", s):
        return False
    # 未配对的 $$ —— 截断常见症状
    if s.count("$$") % 2 == 1:
        return False

    # ── 排斥 3: 纯数字残片（"1 2" / "3 4" 这种被拆散的编号）──
    if re.fullmatch(r"[\d\s.,;\-]{1,12}", plain):
        return False

    # ── 排斥 4: 太短且无数学内容 ──
    if len(plain) < 4 and not re.search(r"[=<>]|\\", s):
        return False

    # ── 排斥 5: 光秃秃的运算符收尾（"...=" 被切断）──
    if re.search(r"[=+\-<>\s]$", s):
        return False

    # 证明类结论
    if re.search(r"(略|证毕|得证|命题成立|结论成立)$", plain):
        return True
    # 含数学结果特征
    if re.search(r"\d", plain):
        return True
    if re.search(r"(不存在|存在但不|无解|唯一|不能|收敛|发散|正交|对称|可逆)", plain):
        return True
    return False


def extract_one_solution(block):
    """
    从一个解答题的正文里抠出结论。
    返回 (answer, confidence, evidence)
      confidence: 'high' = 明确的"故/因此/所以"结论句 且 像答案
                  'mid'  = 末尾独立公式
                  'low'  = 兜底
    """
    txt = block

    # ---- 策略 1: 最后的 "故/因此/所以/即" 结论 ----
    hits = list(re.finditer(r"(?:^|[\n。.；;])\s*(故|因此|所以|亦即|即)\s*([^\n。;；]{1,160})", txt))
    if hits:
        for m in reversed(hits[-6:]):
            cand = clean_conclusion(m.group(2))
            if is_resultlike(cand):
                return cand, "high", m.group(0).strip()[:80]

    # ---- 策略 2: 最后一个独立公式块 $$ ... $$ ----
    blocks = re.findall(r"\$\$(.+?)\$\$", txt, re.S)
    for last in reversed(blocks[-3:]):
        parts = re.split(r"=|\\Rightarrow", last)
        cand = clean_conclusion(parts[-1] if parts else last)
        if is_resultlike(cand):
            return cand, "mid", "末尾公式"

    # ---- 策略 3: 含【答案】的块（2024 版式）----
    m = re.search(r"【答案】\s*([^\n]{2,160})", txt)
    if m:
        cand = clean_conclusion(m.group(1))
        if is_resultlike(cand):
            return cand, "high", "【答案】块"

    # ---- 策略 4: 最后几行非空文本 ----
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    for l in reversed(lines[-4:]):
        if l.startswith("$$") or l.startswith("#"):
            continue
        cand = clean_conclusion(l)
        if is_resultlike(cand):
            return cand, "low", l[:80]

    return "", "none", ""


def _essay_body(text):
    """
    切出解答题正文区。两种版式：
      A. 2000+ ：`三、解答题` … 结束（或遇到 `四、`）
      B. 1987–1999：`三、` … 直到 `填空题`/`选择题` 节（老卷把填选放最后）
    """
    m = SEC_ESSAY.search(text)
    if m:
        rest = text[m.end():]
        m2 = SEC_NEXT.search(rest)
        return rest[:m2.start()] if m2 else rest

    # 老卷：找"填空题/选择题"节……但注意老卷开头也可能是填空题，
    # 所以要找编号 >= 3 的填选节（即最后的收尾节），没有就退到文末。
    heads = list(SEC_CN_ANY.finditer(text))
    if not heads:
        return ""

    def num_of(mm):
        r = re.match(r"^#*\s*([一二三四五六七八九十]+)、", mm.group(0))
        return CN_NUM.get(r.group(1), 0) if r else 0

    # 选第一个编号 >= 3 的节作为起点
    start = None
    for mm in heads:
        if num_of(mm) >= 3:
            start = mm
            break
    if start is None:
        return ""

    # 终点：起点之后，编号 >= 3 且是填空题/选择题的节
    end = len(text)
    for mm in SEC_TAIL.finditer(text):
        if mm.start() > start.start() and num_of(mm) >= 3:
            end = mm.start()
            break
    # 若该卷把填选放最后（1987/1995），上面会命中；否则退到文末
    return text[start.start():end]


def _marks_cn(body):
    """
    老卷版式：每个解答题的起点是中文数字（可能带 #），或裸【解】。
    返回 [(pos, qnum, content_start)]，qnum 从 1 开始按出现顺序编。
    """
    marks = []
    for mm in Q_SOL_CN.finditer(body):
        cn = mm.group(1)
        num = CN_NUM.get(cn)
        if num is None:
            continue
        marks.append((mm.start(), num, mm.end()))
    if marks:
        return marks
    # 退路：一连串裸【解】
    for i, mm in enumerate(Q_SOL_BARE.finditer(body), start=1):
        marks.append((mm.start(), i, mm.end()))
    return marks


# 2022 风格：`17 设函数…` → `解 …`（无括号无点，序号裸行首）
Q_SOL3 = re.compile(r"^#*\s*(\d{1,2})\s+(?=\S)", re.M)
# 2023 风格：`(17)(本题满分10分)` 然后 `【解析】…`
Q_SOL4 = re.compile(r"^#*\s*[（(]\s*(\d{1,2})\s*[）)]\s*[（(]?本题满分", re.M)
# 2024 风格：`【17】 （本题满分 10分）`
Q_SOL5 = re.compile(r"^#*\s*【\s*(\d{1,2})\s*】", re.M)


def _marks_loose(body):
    """
    近三年各自不同的版式，统一成一个宽松的"题号起点"列表：
      2022 → `17 设函数…`         （裸序号）
      2023 → `(17)(本题满分10分)`  （题号+满分）
      2024 → `【17】 （本题满分）` （方括号题号）
    按位置排序后合并，并做去重。
    """
    cand = []
    for rx in (Q_SOL4, Q_SOL5):
        for mm in rx.finditer(body):
            cand.append((mm.start(), int(mm.group(1)), mm.end()))
    if not cand:
        # 2022：裸序号开头，且该行不像公式/选项
        for mm in Q_SOL3.finditer(body):
            n = int(mm.group(1))
            if 1 <= n <= 30:
                tail = body[mm.end():mm.end() + 24]
                if re.match(r"^[（(]?\s*(本题满分|\(本)", tail):
                    cand.append((mm.start(), n, mm.end()))
                elif not tail.startswith("$"):
                    cand.append((mm.start(), n, mm.end()))
    cand.sort()

    # 去重：同一题号只留第一个起点
    seen, out = set(), []
    for st, n, en in cand:
        if n in seen:
            continue
        seen.add(n)
        out.append((st, n, en))
    return out


def parse_year(year, text):
    """返回 {题号: {ans, conf, ev}}"""
    body = _essay_body(text)
    if not body:
        return {}

    # 找所有题起点：数字版式优先，没有就用老卷版式
    marks = []
    for rx in (Q_SOL, Q_SOL2):
        for mm in rx.finditer(body):
            marks.append((mm.start(), int(mm.group(1)), mm.end()))
    if not marks:
        marks = _marks_cn(body)
    if not marks:
        marks = _marks_loose(body)
    if not marks:
        return {}
    marks.sort()

    out = {}
    for i, (st, num, en) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        blk = body[en:nxt]
        if len(blk.strip()) < 20:
            continue
        ans, conf, ev = extract_one_solution(blk)
        if ans and not (num in out and out[num]["conf"] == "high"):
            out[num] = {"ans": ans, "conf": conf, "ev": ev}
    return out


def main():
    if not os.path.isdir(SRC_DIR):
        print(f"[X] 目录不存在: {SRC_DIR}")
        return 1

    all_out = {}
    print(f"{'年':<6}{'解答题节':>8}{'抽出结论':>10}  置信度分布")
    print("-" * 62)
    for f in sorted(os.listdir(SRC_DIR)):
        if not f.endswith(".md") or f.startswith("README"):
            continue
        try:
            year = int(f[:4])
        except ValueError:
            continue
        text = open(os.path.join(SRC_DIR, f), encoding="utf-8", errors="replace").read()
        got = parse_year(year, text)
        if not got:
            print(f"{year:<6}{'—':>8}{0:>10}")
            continue
        dist = {}
        for v in got.values():
            dist[v["conf"]] = dist.get(v["conf"], 0) + 1
        all_out[str(year)] = got
        dstr = " ".join(f"{k}:{v}" for k, v in sorted(dist.items()))
        print(f"{year:<6}{'✓':>8}{len(got):>10}  {dstr}")

    total = sum(len(v) for v in all_out.values())
    print("-" * 62)
    print(f"合计抽出解答题结论: {total} 条")

    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(all_out, fp, ensure_ascii=False, indent=1)
    print(f"已写 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
