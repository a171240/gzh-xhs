# 服务重启协议

## 适用范围
以下变更会影响 repo-local skill 注册或上下文解析：
- `skills/自有矩阵/skill-manifest.json`
- `06-工具/scripts/skill_manifest.py`
- `06-工具/scripts/skill_context_resolver.py`
- `06-工具/scripts/feishu_skill_runner.py`
- 任何被 repo-local skill 默认加载的 context 文件

## 原因
`skill_manifest.py` 使用进程内缓存。长驻进程不会自动感知 manifest 和 alias/context 规则变化。

## 修改后必须重启的进程
- `feishu_kb_orchestrator.py` 以 daemon 方式运行时
- `topic_pipeline.py` 以 daemon 方式运行时
- `ingest_writer_api.py` 所在服务

## 可不重启的场景
- 直接运行的一次性脚本命令
- 新开进程执行的 dry-run / 单次任务

## `--reload` 与人工重启
- `run-ingest-writer-api.ps1` 支持 reload 启动方式，但 reload 只是代码热重载机制，不应代替“配置已重新加载”的验证
- daemon 常驻模式下，最稳妥做法仍是显式重启进程
- 仅在嵌入式调用、测试或临时工具脚本中，才建议直接调用 `skill_manifest.clear_repo_skill_manifest_caches()`
- 对正式长驻服务，不建议只清缓存不重启；仍以重启进程为准

## healthz 约定
- `/internal/healthz` 或 `/healthz` 只能证明服务存活
- 不能证明最新 manifest / context 已经生效
- 生效验证应使用对应 skill 的 dry-run 或实际路由结果
