# -*- coding: utf-8 -*-
"""抽 index.html 内联 JS -> _check.js，交给 node --check 做语法校验。"""
import re, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import INDEX_HTML, TOOLS, NODE, PYTHON   # noqa: E402

HTML = INDEX_HTML

# 抽取出的内联 JS 属于中间产物 —— 写在项目内的 _build/ 里，
# 不再往项目外的 D:\try\ 丢文件（以前就是这样漏出 _check.js 的）。
BUILD = os.path.join(TOOLS, "_build")
os.makedirs(BUILD, exist_ok=True)
JS = os.path.join(BUILD, "_check.js")


def main():
    t = open(HTML, encoding="utf-8").read()
    html = t
    blocks = re.findall(r"<script>(.*?)</script>", t, re.S)
    js = "\n;\n".join(b for b in blocks if b.strip())
    open(JS, "w", encoding="utf-8").write(js)
    print(f"抽出 {len(blocks)} 段内联 JS，共 {len(js)} 字符")

    # 静态断言：关键符号是否存在
    checks = [
        ("esc 辅助函数", "function esc(s)" in js),
        ("mdToHtml 渲染", "function mdToHtml(s)" in js),
        ("__IS_MOCK 标记", "__IS_MOCK" in js),
        ("stem 题干渲染", "class=\"stembox\"" in js),
        ("local 答案分支", "q.src === 'local'" in js),
        # 解答题结论回填 + 质量标注（tools/import_solutions.py 的产物）
        ("sol 答案分支", "q.src === 'sol'" in js),
        ("doubt 存疑提示", "此答案存疑" in js),
        ("doubtbox 样式类", "doubtbox" in js),
        # 错题本 + 答题计时（tools/_test_wrong.py 验证的对象）
        ("错题本标签页", "data-tab=\"wrong\"" in html),
        ("错题本面板", 'id="tab-wrong"' in html),
        ("错题本渲染函数", "async function drawWrong(keepUnsolved)" in js),
        ("标签页数组含 wrong", "'q','paper','grid','stats','hard','wrong'" in js),
        ("drawWrong 被调用", "if(key==='wrong')" in js),
        ("错题本 API", "/api/wrong" in js),
        ("答题计时上报 ms", "ms:Math.round" in js or "ms}" in js),
        ("qShownAt 计时起点", "S.qShownAt = performance.now()" in js),
        ("fmtDur 前端格式化", "function fmtDur(sec)" in js),
        ("错题池跨组", "S.wrongPool" in js),
        # 随机模式真正洗牌（m03827 报「点随机仍是同一张卷的下一题」的回归）
        ("洗牌函数存在", "function shuffleList(arr)" in js),
        ("rand 模式洗牌", "S.mode==='rand') ? shuffleList(list)" in js),
        ("下一题随机跳", "S.mode==='rand' && S.list.length>1" in js),
        ("已抽过集合", "S.randSeen" in js),
        ("离开 rand 清集合", "S.mode==='rand') ? new Set(" in js),
        # 搜索 + 笔记（m02825 方向 E）
        ("搜索输入框", 'id="search"' in html),
        ("搜索防抖", "__searchTimer" in js),
        ("搜索多词 AND", "kw.split(/\\s+/).every" in js),
        ("搜索跨组检索", "if(kw){" in js),
        ("笔记按钮", 'id="btnNote"' in html),
        ("笔记模式过滤", "S.mode==='note'" in js),
        ("笔记编辑框", 'id="noteBox"' in html),
        ("笔记自动保存", "function saveNote(qid, text)" in js),
        ("笔记写入 persist", "s.notes = S.notes" in js),
        ("笔记读入 boot", "S.notes = saved.notes" in js),
        ("notebox 样式类", "notebox" in js),
        ("mock_papers 读取", "mock_papers" in js),
        ("set 套号徽章", "badge mock" in js),
        # LLM 难度接入（tools/llm_difficulty.py 的产物）
        ("qHardness 优先用 llm", "if(q.llm && typeof q.llm.d === 'number')" in js),
        ("qSrc 来源标注", "function qSrc(q)" in js),
        ("qSrc 返回 LLM", "return 'LLM'" in js),
        ("组卷页用 qSrc", "const src = qSrc(q)" in js),
        # 真题结构组卷（选择→填空→解答 分段 + 真卷配额）
        ("PAPER_STRUCT 配额表", "const PAPER_STRUCT =" in js),
        ("新结构 10+6+6", "new: {" in js and "选择题:10" in js),
        ("旧结构 8+6+9", "old: {" in js and "解答题:9" in js),
        ("题型分段顺序", "PAPER_STRUCT_ORDER = ['选择题','填空题','解答题']" in js),
        ("pickByLevel 配额抽题", "function pickByLevel(cand, want, lv)" in js),
        ("结构模式开关", "$('#pStruct').value" in js),
        ("结构分段排序", "PAPER_STRUCT_ORDER\n      .map(tp=>picked.filter" in js.replace(" ", " ")
                                                        or "picked.filter(q=>q.type===tp)" in js),
    ]
    ok = True
    for name, good in checks:
        print(f"   {'✅' if good else '❌'} {name}")
        ok = ok and good

    # CSS 断言
    css = t.split("</style>")[0]
    for name, good in [(".stembox 样式", ".stembox{" in css),
                       (".ansval 样式", ".ansval{" in css),
                       (".doubtbox 样式", ".doubtbox{" in css),
                       (".badge.mock 样式", ".badge.mock{" in css),
                       ("#noteBox 样式", "#noteBox{" in css),
                       ("#search 样式", "#search{" in css)]:
        print(f"   {'✅' if good else '❌'} {name}")
        ok = ok and good

    r = subprocess.run([NODE, "--check", JS], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        print("   ✅ node --check 语法通过")
    else:
        print("   ❌ 语法错误:")
        print((r.stderr or "")[:2000])
        ok = False

    print("\n" + ("✓ 全部通过" if ok else "✗ 存在问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
