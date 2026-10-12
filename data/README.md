# 数据目录

本框架**不含题库数据**，所以这里初始是空的。

- `ztq_bank.db` — 题库本体，首次运行 `app/bank.py` 时自动创建（自动建表）
- `ztq.db` — 站点原始库，用 `tools/` 里的抓取脚本生成
- `images/` — 题图，`tools/fetch_images.py` 下载到这里
- `mock/` — 模拟卷 md 素材

这些文件都被 `.gitignore` 忽略（数据不进版本库）。
