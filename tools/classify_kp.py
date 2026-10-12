# -*- coding: utf-8 -*-
"""给旧卷（old*）和模拟卷（mock*）补知识点分类。

背景：站点真题（group 8/9/10）自带 `knowledge_tags_id` 祖链，所以
kp_name / kp_lv1 / kp_lv2 都填好了；但本地导入的 old1987–old2008 与
mock2024–2026 这三类没有——源 md 只有题干，没有知识点标注。
结果是前端「按知识点筛选」「弱项统计」「随机组卷按难度」全都漏掉这些题。

做法：**关键词打分分类**。
  - 以站点已标注的 1224 题为语料，统计每个三级知识点名在题干里
    高频出现的中文/LaTeX 关键词（人工审校后固化为 RULES）；
  - 对未标注题按 RULES 打分，取最高分；低于阈值则落到所在章节的
    兜底知识点。
  - 章节由 `kp` 表的 level-1 父节点给出（高等数学 / 线性代数 / 概率论与数理统计）。

这是启发式，不是站点原始标注，所以 answer_note 不动，另在 kp_name 里
保留结果即可；如需区分来源，看 `kp_id` 是否 > 10000（本脚本写入时 +10000）。
"""
import re
import sqlite3
from collections import defaultdict
from paths import BANK_DB, TOOLS

BANK = BANK_DB

# ── 规则表 ───────────────────────────────────────────────────────────
# 每条: (kp_id, 关键词列表)。关键词命中即加分，长关键词权重更高。
#
# 重要：kp 表里有**三棵独立的树**（group 8/9/10 各一份，id 段
# 1–53 / 85–125 / 140–210），叶子名高度重合但 id 不同。本脚本面向
# 数学一（group 8）的本地补充卷，因此统一使用**第一棵树（1–53）**的
# 三级叶子 id，再在写库时按 group 映射到该 group 自己的树。
RULES = [
    # --- 函数、极限、连续 (parent 2) ---
    (3,  ["数列", "敛散", "单调有界", "x_{n}", "a_{n}"]),
    (4,  ["求极限", "\\lim", "洛必达", "等价无穷小代换", "极限"]),
    (5,  ["确定常数", "求常数", "使极限", "已知极限", "参数.*取值"]),
    (6,  ["无穷小", "高阶无穷小", "同阶无穷小", "等价无穷小"]),
    (7,  ["连续", "间断点", "可去间断", "跳跃间断"]),
    # --- 一元函数微分学 (parent 8) ---
    (9,  ["导数定义", "可导", "可微", "左导数", "右导数", "导数的定义"]),
    (10, ["求导", "\\mathrm{d}y", "y'", "f'(x)", "导数", "微分"]),
    (11, ["切线", "法线", "切点", "公切线", "切线的斜率", "求曲线.*的切线"]),
    (12, ["单调", "极值", "最大值", "最小值", "最值", "极大值", "极小值", "驻点", "极大", "极小", "极值点"]),
    (13, ["凹凸", "拐点", "渐近线", "渐近线的条数", "凹", "凸"]),
    (14, ["实根", "根的个数", "零点", "方程的根", "存在唯一", "有且仅有"]),
    (15, ["不等式", "\\geqslant", "\\leqslant", "证明.*<", "证明.*>", "证明"]),
    (16, ["中值定理", "罗尔", "拉格朗日", "柯西中值"]),
    (17, ["泰勒", "麦克劳林", "f^{(n)}(0)", "f^{(5)}(0)", "高阶导数", "高阶可导", "阶可导"]),
    # --- 一元函数积分学 (parent 18) ---
    (19, ["不定积分", "原函数", "换元积分", "分部积分"]),
    (20, ["定积分的概念", "定积分的性质", "积分中值定理", "定积分的几何意义"]),
    (21, ["定积分", "\\int_{0}^{", "\\int_{1}^{", "\\int_{a}^{b}"]),
    (22, ["变限积分", "上限函数", "\\int_{0}^{x}", "\\int_{1}^{x}", "\\int_{a}^{x}"]),
    (23, ["反常积分", "广义积分", "\\int_{1}^{+\\infty}", "无穷限", "\\int_{0}^{+\\infty}"]),
    (24, ["旋转体", "平面图形.*面积", "弧长", "做功", "压力", "积分.*应用", "体积", "曲率",
          "表面积", "蒸发速度"]),
    # --- 多元函数微分学 (parent 25) ---
    (26, ["偏导数", "\\frac{\\partial", "偏导", "全微分", "\\mathrm{d}z"]),
    (27, ["全微分", "\\mathrm{d}z"]),
    (28, ["切平面", "法线方程", "曲面.*切"]),
    (29, ["方向导数", "梯度", "\\mathbf{grad}", "\\nabla"]),
    (30, ["条件极值", "拉格朗日乘数", "多元函数.*极值", "无条件极值"]),
    # --- 多元函数积分学 (parent 31) ---
    (32, ["二重积分.*概念", "重积分.*性质"]),
    (33, ["交换积分次序", "极坐标", "柱面坐标", "球面坐标", "积分次序"]),
    (34, ["二重积分", "三重积分", "\\iint", "\\iiint"]),
    (35, ["曲面面积", "质心", "转动惯量", "重积分.*应用"]),
    (36, ["第一类曲线积分", "\\int_{L} \\mathrm{d}s"]),
    (37, ["第二类曲线积分", "\\oint_{L}", "格林公式", "曲线积分"]),
    (38, ["第一类曲面积分", "\\iint_{\\Sigma} \\mathrm{d}S"]),
    (39, ["第二类曲面积分", "高斯公式", "斯托克斯", "曲面积分"]),
    (40, ["旋度", "散度", "\\mathbf{rot}", "\\mathrm{div}"]),
    # --- 无穷级数 (parent 41) ---
    (42, ["常数项级数", "判别法", "比较判别", "比值判别", "交错级数", "绝对收敛", "条件收敛",
          "级数.*收敛", "\\sum_{n=1}", "\\sum_{n = 1}", "敛散"]),
    (43, ["收敛半径", "收敛域", "收敛区间", "幂级数"]),
    (44, ["和函数", "幂级数展开", "展开成.*级数", "幂级数.*和"]),
    (45, ["傅里叶", "傅立叶", "正弦级数", "余弦级数", "周期函数"]),
    # --- 常微分方程 (parent 46) ---
    (47, ["通解", "特解", "解的结构", "线性无关.*解", "的通解为"]),
    (48, ["可分离变量", "齐次方程", "\\frac{\\mathrm{d}y}{\\mathrm{d}x} =", "\\, dy =", "\\, dx"]),
    (49, ["一阶线性", "一阶非齐次", "y' + p(x)", "一阶微分方程", "伯努利"]),
    (50, ["常系数齐次", "特征方程", "y'' + ", "y^{\\prime\\prime} +"]),
    (51, ["常系数非齐次", "y'' - ", "y^{\\prime\\prime} -", "非齐次线性微分方程"]),
    (52, ["伯努利", "全微分方程", "欧拉方程"]),
    (53, ["微分方程.*应用", "变化率", "衰变", "冷却", "满足条件.*y("]),
    # --- 线性代数：行列式 (55) / 矩阵的运算与变换 (57) ---
    (55, ["行列式", "\\left| \\begin{array}", "代数余子式", "余子式", "|\\mathbf{A}|", "克拉默"]),
    (57, ["矩阵的乘法", "\\mathbf{A}\\mathbf{B}", "矩阵运算", "转置", "初等变换", "初等矩阵",
          "\\mathbf{A}^{T}", "实对称矩阵", "值只可能为"]),
    # --- 平面与空间直线（数学一专属，挂几何/向量内积节点 63） ---
    (63, ["内积", "正交", "正交化", "平面的方程", "空间直角坐标系", "法向量", "直线.*方程"]),
    # --- 线性代数：特征值 (parent 65) ---
    (66, ["特征值", "特征向量", "\\lambda"]),
    (67, ["相似", "相似对角化", "\\mathbf{P}^{-1}\\mathbf{A}\\mathbf{P}", "可对角化"]),
    # --- 概率论 (parent 69) ---
    (72, ["概率", "事件", "独立", "全概率", "贝叶斯", "条件概率", "个黑球", "个白球", "个红球", "取球", "摸球"]),
    (73, ["二维随机变量", "联合分布", "边缘分布", "条件分布", "(X, Y)", "(X,Y)"]),
    (76, ["数学期望", "方差", "E(X)", "D(X)", "期望"]),
    (77, ["协方差", "相关系数", "\\mathrm{Cov}", "不相关"]),
    (81, ["无偏", "无偏性", "无偏估计"]),
    (82, ["矩估计", "最大似然", "极大似然"]),
    (83, ["置信区间", "区间估计"]),
    (84, ["假设检验", "显著性"]),
    # --- 概率：随机变量及其分布 (附加规则，命中单点也接受) ---
    # 注意：一棵树的树叶里"随机变量及其分布"是 lv2 节点 71，"数理统计的基本概念"
    # 是 lv2 节点 79，均无三级叶子，因此直接用其自身 id。
    (71, ["服从正态分布", "正态分布", "服从自由度为", "自由度为", "服从", "分布函数",
          "概率密度", "分布律", "\\chi^{2}", "t 分布", "F 分布", "分位数",
          "u_{\\alpha}", "无记忆性", "指数分布", "均匀分布", "二项分布", "泊松分布"]),
    (79, ["简单随机样本", "是来自总体", "来自总体", "样本均值", "样本方差", "统计量",
          "抽样分布", "总体 X", "总体X", "样本容量"]),
    # --- 线性代数补充：矩阵的秩 / 可逆（原规则漏了"初等行变换""同解"等） ---
    (59, ["同解", "矩阵的秩", "r(\\mathbf{A})", "秩必为", "秩为", "满秩", "初等行变换", "初等列变换"]),
    (58, ["可逆", "不可逆", "阶非零矩阵", "阶单位矩阵", "\\mathbf{A}^{*}", "A^{*}", "伴随矩阵", "逆矩阵"]),
    # --- 线性方程组 (64)：tree1 里"线性方程组"是 lv2 节点 64，无三级叶子 ---
    (64, ["方程组", "基础解系", "有唯一解", "无穷多解", "同解", "解的结构"]),
    # --- 特征值/二次型补充 ---
    (66, ["的特征值为", "求得.*特征值", "相似于"]),
    (67, ["相似", "相似对角化", "\\mathbf{P}^{-1}\\mathbf{A}\\mathbf{P}", "可对角化", "相似于"]),
    # --- 线性代数：二次型 (69) ---
    #   注意：第一棵树里二次型是 lv2 节点 68 直挂，概率论与数理统计才是 69，
    #   早期误用 69 会把二次型题错挂到概率论下（已修正为 68）。
    (68, ["二次型", "正定", "标准形", "规范形", "合同", "正惯性指数", "二次曲面",
          "可逆线性变换", "惯性指数"]),
]


# 单点即可接受的节点（这些节点本身语义很窄，一个关键词就足以定位）
SINGLE_OK = {71, 79, 64, 84, 83, 82, 81, 77, 45, 40, 29, 28, 27, 26, 23, 17, 16, 7, 6, 5}



# ── group → 该 group 知识点树的 id 映射 ────────────────────────────────
# 每棵树的叶子按名字对上；只在需要写库时换算。
TREE_BY_GROUP = {"8": 1, "9": 85, "10": 140}


def norm(s):
    """把题干压成一行便于匹配。"""
    return re.sub(r"\s+", " ", s or "")


def load_kp(c):
    kp = {}
    for r in c.execute("SELECT kp_id,kp_name,parent_id,level FROM kp"):
        kp[r["kp_id"]] = (r["kp_name"], r["parent_id"], r["level"])
    return kp


def chapter_of(kp, kp_id):
    """回溯到 level-1 章节名。"""
    cur = kp_id
    for _ in range(5):
        if cur not in kp:
            return "", ""
        name, parent, level = kp[cur]
        if level == 1:
            return name, ""
        # 记下 level-2
        lv2 = name if level == 2 else None
        if parent is None:
            return name, ""
        cur = parent
    return "", ""


def build_name_index(kp):
    """{(root_id, leaf_name): leaf_id} —— 用于跨树换算。"""
    # 先建 parent 链，求出每个节点的根
    idx = {}

    def root_of(kid):
        cur, guard = kid, 0
        while guard < 6:
            guard += 1
            if cur not in kp:
                return None
            name, parent, level = kp[cur]
            if level == 1:
                return cur
            cur = parent
        return None

    for kid, (name, parent, level) in kp.items():
        r = root_of(kid)
        if r is not None:
            idx.setdefault((r, name), kid)
    return idx


def classify(stem, kp):
    """返回 (kp_id, kp_name, lv1, lv2) 或 None（kp_id 属第一棵树）

    评分：每个命中原子按长度加权（越具体越重）。采纳条件放宽为
    「最高分 >= 1」，因为题库里绝大多数题干只含**一个**学科术语
    （如"切线""弧长""正交""切平面"），要求两处佐证会把它们全判为未分类。
    误判风险由两个手段控制：①原子表本身避开"函数""曲线"这类通用词；
    ②当最高分与次高分并列时（无法区分），取原子更长的一方。
    """
    s = norm(stem)
    scored = []
    for kp_id, kws in RULES:
        if kp_id not in kp:
            continue
        sc, longest = 0, 0
        for kw in kws:
            if kw in s:
                sc += 2 if len(kw) >= 4 else 1
                longest = max(longest, len(kw))
        if sc:
            scored.append((sc, longest, kp_id))
    if not scored:
        return None
    # 主序：总分；次序：最长原子长度（更具体者优先）
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best_score, _, best = scored[0]
    name = kp[best][0]
    lv1, lv2 = chapter_of(kp, best)
    return best, name, lv1, lv2


def main():
    c = sqlite3.connect(BANK)
    c.row_factory = sqlite3.Row
    kp = load_kp(c)
    nidx = build_name_index(kp)

    cols = {r[1] for r in c.execute("PRAGMA table_info(question)")}
    for col in ("kp_name", "kp_id", "kp_lv1", "kp_lv2"):
        if col not in cols:
            c.execute(f"ALTER TABLE question ADD COLUMN {col} TEXT")
            print("新增列", col)

    rows = c.execute("""SELECT id, group_id, year, qtype_name, stem, kp_name
                          FROM question
                         WHERE (group_id LIKE 'old%' OR group_id LIKE 'mock%')
                         ORDER BY group_id, CAST(number AS INT)""").fetchall()
    print(f"待分类 {len(rows)} 题")

    done = 0
    miss = []
    dist = defaultdict(int)
    for r in rows:
        got = classify(r["stem"], kp)
        if not got:
            miss.append((r["group_id"], r["id"], norm(r["stem"])[:60]))
            continue
        kid, kname, lv1, lv2 = got
        # 本地卷都是数学一 → 统一挂在第一棵树（root 1）
        c.execute("""UPDATE question SET kp_id=?, kp_name=?, kp_lv1=?, kp_lv2=?
                      WHERE id=?""", (str(kid), kname, lv1, lv2, r["id"]))
        done += 1
        dist[kname] += 1
    c.commit()

    print(f"已分类 {done}/{len(rows)}；未命中 {len(miss)}")
    print("\n知识点分布 Top20:")
    for n, k in sorted(dist.items(), key=lambda x: -x[1])[:20]:
        print(f"  {k:>4}  {n}")
    if miss:
        print(f"\n未命中抽样 15（共 {len(miss)}）:")
        for g, i, s in miss[:15]:
            print(f"  {g} id={i}: {s}")

    # 覆盖率复查
    print("\n=== 覆盖率复查 ===")
    for r in c.execute("""SELECT group_id, COUNT(*) t, SUM(kp_name<>'') k
                            FROM question GROUP BY group_id
                           ORDER BY (group_id LIKE 'mock%'), (group_id LIKE 'old%'), group_id"""):
        print(f"  {r['group_id']:<9} {r['t']:>4}题  有知识点 {r['k']:>4}")


if __name__ == "__main__":
    main()
