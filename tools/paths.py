# -*- coding: utf-8 -*-
"""项目路径中心 —— 所有脚本一律从这里取路径，不再硬编码。

目录约定：
考研刷题/
      app/        运行时的网页与索引（bank.py / index.html / index.json / vendor/）
      data/       数据（ztq.db 站点原始库、ztq_bank.db 题库、images/、mock/）
      tools/      全部脚本（本目录）
      tools/_build/  脚本中间产物（_answers_raw.json / _solutions_raw.json）
      capture/    抓包证据（index.js 等）
      docs/       截图

这样任何脚本无论从哪个工作目录启动，都能定位到同一份数据。
"""
import os
import sys

# tools/paths.py -> tools -> 考研刷题
TOOLS = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(TOOLS)

APP = os.path.join(PROJ, "app")
DATA = os.path.join(PROJ, "data")
BUILD = os.path.join(TOOLS, "_build")
CAPTURE = os.path.join(PROJ, "capture")
DOCS = os.path.join(PROJ, "docs")

# 数据库
ZTQ_DB = os.path.join(DATA, "ztq.db")            # 从站点抓下来的原始题库
BANK_DB = os.path.join(DATA, "ztq_bank.db")      # 本地题库（含学习记录）

# 前端
INDEX_HTML = os.path.join(APP, "index.html")
INDEX_JSON = os.path.join(APP, "index.json")
VENDOR = os.path.join(APP, "vendor")

# 静态资源
IMAGES = os.path.join(DATA, "images")
MOCK = os.path.join(DATA, "mock")

# 脚本中间产物
ANSWERS_RAW = os.path.join(BUILD, "_answers_raw.json")
SOLUTIONS_RAW = os.path.join(BUILD, "_solutions_raw.json")

# ---------------------------------------------------------------------------
# 外部资料源（用户自己的考研资料，只读）
#
# 这里**不写死盘符**：默认在项目根下的 _study/ 找，找不到再按环境变量
# ZTQ_STUDY 指定的位置找。把资料放到别处时设 ZTQ_STUDY 即可。
# 项目本体（app/data/tools/capture/docs）全部由 __file__ 推导，
# 整个文件夹可以随意改名、搬到任何盘、拷给别人，无需改代码。
# ---------------------------------------------------------------------------
def _first_dir(name, *cands):
    """按顺序返回第一个存在的目录；都不存在时返回第一个候选（便于报错）。"""
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return cands[0] if cands else name


def _find_study():
    """考研资料根目录 —— 优先环境变量，其次项目内 _study/。

    把你的考研资料（真题 md / 解析 md / 模拟卷）放到项目内的 _study/，
    或用环境变量 ZTQ_STUDY 指向别处。
    """
    env = os.environ.get("ZTQ_STUDY")
    cands = [env, os.path.join(PROJ, "_study")]
    return _first_dir("_study", *[c for c in cands if c])


STUDY = _find_study()
STUDY_JIEXI = _first_dir("解析", os.path.join(STUDY, "解析"),
                         os.path.join(STUDY, "jiexi"))
STUDY_ZHENTI = _first_dir("真题", os.path.join(STUDY, "真题", "原始卷"),
                          os.path.join(STUDY, "zhenti"))
STUDY_MOCK = _first_dir(
    "模拟卷", os.path.join(STUDY, "模拟卷", "李艳芳预测三套卷", "可检索文本"),
    os.path.join(STUDY, "moni"))


def _find_exe(env_name, *cands):
    """按环境变量 -> 候选绝对路径 -> PATH 上的命令名 依次探测可执行文件。"""
    import shutil
    env = os.environ.get(env_name)
    if env and os.path.isfile(env):
        return env
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for c in cands:
        name = os.path.basename(c) if c else ""
        if name.endswith(".exe"):
            name = name[:-4]
        if name:
            got = shutil.which(name)
            if got:
                return got
    return (cands[0] if cands else env_name)


PYTHON = _find_exe("ZTQ_PYTHON",
                   sys.executable,
                   os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs",
                                "Python", "Python312", "python.exe"),
                   os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs",
                                "Python", "Python311", "python.exe"),
                   "python.exe")
NODE = _find_exe("ZTQ_NODE",
                 "node.exe")
CHROME = _find_exe("ZTQ_CHROME",
                   r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   "chrome.exe")

# 服务地址（换端口时设 ZTQ_PORT 或 ZTQ_URL）
PORT = int(os.environ.get("ZTQ_PORT") or 8899)
BASE_URL = os.environ.get("ZTQ_URL") or f"http://127.0.0.1:{PORT}"


def ensure_dirs():
    """确保运行时目录存在（脚本首次运行时调用）。"""
    for d in (APP, DATA, BUILD, CAPTURE, DOCS, IMAGES):
        os.makedirs(d, exist_ok=True)


if __name__ == "__main__":
    for k in ("PROJ", "APP", "DATA", "TOOLS", "BUILD", "ZTQ_DB", "BANK_DB",
              "INDEX_JSON", "IMAGES", "MOCK", "ANSWERS_RAW", "SOLUTIONS_RAW",
              "STUDY", "STUDY_JIEXI", "STUDY_ZHENTI", "STUDY_MOCK",
              "PYTHON", "NODE", "CHROME", "PORT", "BASE_URL"):
        v = globals()[k]
        hint = ""
        if k.startswith("STUDY") or k in ("PYTHON", "NODE", "CHROME"):
            if not os.path.exists(v):
                hint = "   <-- 不存在"
        print(f"{k:14} = {v}{hint}")
