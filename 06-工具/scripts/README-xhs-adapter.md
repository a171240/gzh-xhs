# XHS Adapter（零侵入接入层）

## 目标
- 以适配层方式接入 `XiaohongshuSkills`，不侵入现有主流程。
- 默认关闭，通过 `XHS_ADAPTER_ENABLED=true` 显式启用。
- 提供账号隔离、锁、防 SSRF、CDP 远程限制、日志脱敏能力。

## 文件结构
- `adapters/xhs/contract.py`：动作请求/响应协议。
- `adapters/xhs/security_guard.py`：账号、路径、删除、URL 安全校验。
- `adapters/xhs/lock.py`：单机账号+动作互斥锁。
- `adapters/xhs/client.py`：白名单动作执行客户端。
- `xhs_adapter_cli.py`：命令行入口。
- `config/xhs_adapter.example.json`：配置样例。

## 配置
1. 复制样例：
```bash
cp 06-工具/scripts/config/xhs_adapter.example.json 06-工具/scripts/config/xhs_adapter.json
```
2. 设置环境变量：
```bash
XHS_ADAPTER_ENABLED=true
XHS_ADAPTER_CONFIG=06-工具/scripts/config/xhs_adapter.json
```
3. 配置外部执行命令（二选一）：
- 全局命令：`XHS_ADAPTER_CMD`
- 分动作命令：`XHS_ADAPTER_CMD_PUBLISH` / `..._SEARCH` / `..._DETAIL` / `..._COMMENT` / `..._CONTENT_DATA`

建议同时配置：
- `runner.allowed_binaries`：可执行程序白名单（默认仅 Python 系）。
- `runner.allowed_executable_roots`：可执行程序路径白名单（可选，启用后必须命中）。
- `lock_stale_after_sec`：陈旧锁回收阈值（秒）。

命令支持占位符：
- `{request_file}`：请求 JSON 文件路径
- `{action}`：动作名
- `{account}`：账号
- `{trace_id}`：追踪 ID

## CLI 使用
```bash
python 06-工具/scripts/xhs_adapter_cli.py \
  --action publish \
  --account acc1 \
  --input payload.json \
  --dry-run
```

输出协议：
```json
{
  "ok": true,
  "action": "publish",
  "account_id": "acc1",
  "trace_id": "trace-001",
  "status": "dry_run",
  "data": {},
  "error": null
}
```

## publish_action_runner 接入方式
- 仅当：
  - `XHS_ADAPTER_ENABLED=true`
  - `platform` 为 `xhs`
  - `adapter_action` 或 `xhs_action` 命中白名单
- 才会走适配层分支。
- 否则完全走原逻辑。

## 安全默认策略
- `account_id` 必须匹配 `[a-zA-Z0-9_-]{1,64}`。
- 所有 profile/lock 路径必须在受控根路径内。
- URL 默认拒绝私网/环回/保留地址/元数据地址。
- 远程 CDP 默认禁用，仅允许本地 `127.0.0.1/localhost`。
- 日志会脱敏 `token/cookie/authorization`。
- runner 执行命令受白名单控制，避免任意命令注入式执行。
- 锁文件支持陈旧锁自动回收（超时或 PID 不存活）。

## 降级和回滚
- 一键关闭：`XHS_ADAPTER_ENABLED=false`
- 关闭后 `publish_action_runner.py` 自动回到既有链路，不需要代码回滚。
