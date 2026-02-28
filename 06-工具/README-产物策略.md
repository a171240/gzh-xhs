# 06-工具 产物策略

本文件定义工具目录的保留与清理边界，目标是：
- 保留源码与可运行环境
- 降低高波动运行产物带来的版本噪音

## 必须保留

- 源码与配置：`06-工具/scripts/`、`06-工具/desktop-app/`、`06-工具/内容抓取/url-reader/`、`06-工具/MediaCrawler/`
- 可运行环境：如 `.venv`、`node_modules`（用于本地即开即用）
- 发布包与可执行文件：如 `release/`、`URLReader.exe`

## 可清理（高波动运行产物）

- 抓取临时目录：`06-工具/内容抓取/抓取内容/runs/_tmp/`
- 浏览器缓存目录（Cache、Code Cache、GPUCache）
- 崩溃报告目录（Crashpad）
- 运行日志（`*.log`）
- Python 字节码缓存（`__pycache__`）

上述内容已通过根 `.gitignore` 屏蔽，默认不纳入版本控制。

## 禁止提交

- 临时调试文件（`*.tmp`、`*.temp`）
- 本地编译中间产物（`build/`、`dist/`、`.pyinstaller-build/`）

## 维护建议

- 每次新增工具子目录时，先补一条“保留/清理/禁止提交”说明。
- 若某类运行产物需要长期保留，先在该子目录补 README，再精确放开 `.gitignore`。
