# 刷题框架

一个**本地离线**考研数学刷题工具的**代码框架**：程序全部就位，但**不含任何题目数据**。

> ⚠️ 这是框架版：数据库是空的（只有表结构），`data/images/` 与 `data/mock/` 是空目录。
> 打开网页会看到「空题库 · 尚未导入题目」。请按下方「导入数据」自行填入题目。

**功能一览**：六个标签页（题库 / 随机组卷 / 答题卡 / 统计 / 难题榜 / 错题本）、
KaTeX 公式渲染、SM-2 间隔重复、答题计时、题干全文搜索、本地笔记、
按真题结构组卷（10选+6填+6解 / 8选+6填+9解）、轻量深色/浅色双主题。

**技术栈**：Python 标准库（`http.server` + `sqlite3`，**零第三方依赖**）
+ 单文件原生 JS 前端 + 本地 KaTeX。整个服务约 5 MB 内存，秒级启动。

## 快速开始

```
双击  启动题库.bat
或    python app/bank.py serve
```
浏览器打开 **http://127.0.0.1:8899/**

首次打开是空题库，属正常 —— 见下一节。

## 导入数据

框架自带可运行的空库，可以用三种方式填空：

### 方式 A：放入现成题库（最快）

如果手上有已建好的题库（比如从别的机器拷来的），直接放这两个东西进来即可：

```
data/ztq_bank.db     题库本体（含学习记录）
app/index.json       前端读的索引
data/images/         题图目录
```

放好后重启服务，页头就会显示真实题数。

> 只要题目、不要别人学习记录时：拷完 `ztq_bank.db` 后清一下 `record` 与 `srs`
> 两张表（这两张表存的就是作答记录和间隔重复状态）：
> ```
> sqlite3 data/ztq_bank.db "DELETE FROM record; DELETE FROM srs;"
> ```
> 清完再用 `python tools/export_index.py` 重新导出索引。

### 方式 B：自己抓站点重建

需要 `data/ztq.db`（站点原始库）作为输入，然后依次跑：

```bash
python tools/enumerate.py          # 枚举分组与知识点树（需联网）
python tools/fetch_answers.py      # 拉取答案
python tools/fetch_images.py       # 下载题图
python tools/rebuild_bank.py       # 一键重建：build → 回填 → 模拟卷 → 导出
```

注意 `tools/rebuild_bank.py` 的**顺序依赖**（脚本已按序串好，别单独跑 `build_bank.py`，
它会先删库）：

```
build_bank.py        从 ztq.db 生成 ztq_bank.db 骨架（会先删库）
import_answers.py    回填本地解析答案
import_mock.py       导入模拟卷
export_index.py      重新导出 index.json
```

### 方式 C：只导入自己的资料

把本地 md 解析放到资料源目录（默认 `_study/`，或用 `ZTQ_STUDY` 指定），再跑
`import_old.py`（老真题）/ `import_mock.py`（模拟卷）/ `extract_answers.py`（答案抽取）。
每个脚本都能单独跑，详见 `tools/` 下各脚本的 docstring。

## 目录结构

```
刷题框架/
├─ README.md              本文件
├─ LICENSE                MIT（仅覆盖代码与文档）
├─ 启动题库.bat            双击启动（自动定位解释器与服务入口）
├─ app/                   运行时（网页 + 服务）
│  ├─ bank.py             刷题工具（CLI + 网页服务）
│  ├─ index.html          网页界面（六个标签页）
│  ├─ index.json          空索引（导入数据后生成）
│  └─ vendor/katex/       KaTeX 公式渲染（本地，离线可用）
├─ data/                  数据（**当前为空**）
│  ├─ README.md           各数据文件说明
│  ├─ ztq_bank.db         题库（首次运行时自动创建表结构，0 题）
│  ├─ ztq.db              站点原始库（用抓取脚本生成）
│  ├─ images/             空目录（放题图）
│  └─ mock/               空目录（放模拟卷 md）
├─ tools/                 全部脚本（含 paths.py 路径中心）
├─ capture/               空目录（抓包证据放这）
└─ docs/                  截图
```

## 空库也能干的事

框架不代表不能跑 —— 服务、界面、KaTeX、所有 CLI 命令都能启动，
只是「没有匹配的题目」。已验证的空态行为：

| 项目 | 空库时的表现 |
|---|---|
| 网页首页 | 200，页头显示「空题库 · 尚未导入题目（见 README「导入数据」）」 |
| 题目区 | 显示「没有匹配的题目」 |
| 六个标签页 | 全部可点开，不报错（题库/组卷/答题卡/统计/难题榜/错题本） |
| `/api/progress` 等接口 | 正常返回空结构 |
| `/api/index.json` 缺失时 | **返回空索引而不是 500**，页面正常打开（不白屏） |
| CLI 全部 16 个子命令 | 全部 exit 0，`stats` 显示「⚠ 题库为空」 |

> 说明：空白框架能做到这些，是因为专门修了两处「空数据崩溃」——
> `index.html` 的 `boot()`（原 `gs[0][0]` 在无分组时抛
> `TypeError: Cannot read properties of undefined`，导致整个 boot 中断、
> 标签页点击都不绑定）和 `bank.py` 的 `cmd_stats`（原 `ga/tot` 除零崩溃）。

## 命令行

```bash
python app/bank.py list    [--group 8] [--year 2025] [--kp 微分] [--hard]
python app/bank.py show    <qid>
python app/bank.py quiz    [-n 20] [--group 8] [--due] [--hard]
python app/bank.py wrong   [--unsolved-only]
python app/bank.py paper   -n 22 --struct auto
python app/bank.py stats   [--group 8]
python app/bank.py serve   [--port 8899]
```

## 数据表结构

`data/ztq_bank.db` 已建好全部表，导入数据时按这个结构填：

| 表 | 用途 |
|---|---|
| `question` | 题目（id / 分组 / 年份 / 题型 / 分值 / 答案 / 知识点 / 题干 / 题图路径 …） |
| `record` | 作答记录（qid / 时间 / 对错 / 耗时 elapsed / 最近答错 last_wrong） |
| `srs` | 间隔重复状态（streak / ease / due） |
| `kp` | 知识点树（三级：高等数学 / 线性代数 / 概率统计） |
| `paper` | 试卷元数据 |
| `meta` | 键值元信息 |

字段的完整定义见 `tools/build_bank.py` 里的 `CREATE TABLE` 语句。

## 路径约定

这是一个**自包含的独立文件夹**，可以随便改名、搬到任何盘、拷给别人。
项目自身路径（`app/` `data/` `tools/` `capture/` `docs/`）全部由
`tools/paths.py` 的 `PROJ = os.path.dirname(TOOLS)` 从脚本位置推导，
**代码里没有任何写死的盘符**。

只有三类东西是「环境相关」的，全部是自动探测 + 可用环境变量覆盖：

| 项 | 默认行为 | 覆盖方式 |
|---|---|---|
| 考研资料源 | 依次找 `$ZTQ_STUDY` → 项目内 `_study/` → 旧的 `D:\学习\考研\数学一` | `set ZTQ_STUDY=...` |
| Python / Node / Chrome | 环境变量 → 常见安装位置 → 从 `PATH` 上找 | `ZTQ_PYTHON` / `ZTQ_NODE` / `ZTQ_CHROME` |
| 端口 | `8899` | `ZTQ_PORT` 或 `ZTQ_URL` |

查当前实际生效的值：`python tools/paths.py`（不存在的路径会标 `<-- 不存在`）。

## 验证

```bash
python tools/check_ui.py        # 界面静态断言
python tools/smoke_cli.py       # CLI 冒烟测试
node   tools/skeleton_check.js  # 空库渲染检查（15 项，需先启服务）
```

## 说明

- 本框架**不含任何题目、答案、题图或学习记录** —— 数据一律由你自己导入。
- 网页上的作答与判分**全部在本地**，不会向任何服务器发送数据。
- 你自行导入的题目内容，版权归各自权利人所有。**仅供个人学习使用，请勿二次分发。**
- 代码以 [MIT 许可证](LICENSE) 发布。

## 已知局限

- 目前只验证了「数学一」一种卷型。数学二/三需自己抓取导入（框架本身不分卷型）。
- 题目来源与题图采集脚本面向特定站点结构，站点改版后需要相应调整。
- 前端**全量加载** `index.json`（1000 题约 0.8 MB）—— 本地自用无感，题量上万时需改成分页/分片。

