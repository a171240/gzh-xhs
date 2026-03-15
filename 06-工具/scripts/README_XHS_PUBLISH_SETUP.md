# 小红书发布配置

## 目标

让 `publish_action_runner.py` 和 `xhs_pipeline.py` 在非 dry-run 下可直接调用 BitBrowser 里的已登录小红书账号。

## 必填环境变量

先以账号维度绑定 BitBrowser profile：

- `XHS_A_BITBROWSER_PROFILE_ID`
- `XHS_B_BITBROWSER_PROFILE_ID`
- `XHS_C_BITBROWSER_PROFILE_ID`

全局 BitBrowser API：

- `BITBROWSER_API_BASE`
- `BITBROWSER_API_KEY`

可选：

- `XHS_A_SELECTORS_PATH`
- `XHS_B_SELECTORS_PATH`
- `XHS_C_SELECTORS_PATH`
- `XHS_LOGIN_TIMEOUT_SEC`
- `XHS_SLOW_MO_MS`

可以直接参考 [`.env.xhs.publish.example`](/E:/公众号内容生成/06-工具/scripts/.env.xhs.publish.example)。

## Selectors

默认 selectors 已内置回落到：

- [`selectors.xhs.sample.json`](/E:/公众号内容生成/06-工具/scripts/config/selectors.xhs.sample.json)

如果某个账号界面有差异，单独设置：

- `XHS_A_SELECTORS_PATH`
- `XHS_B_SELECTORS_PATH`
- `XHS_C_SELECTORS_PATH`

## BitBrowser 准备

1. 启动 BitBrowser。
2. 确认本地 API 可访问，默认是 `http://127.0.0.1:54345`。
3. 在 BitBrowser 中分别打开 A/B/C 对应 profile，并确保已经登录小红书创作中心。
4. 记录每个 profile 的 `profile_id`，写入账号级环境变量。

## 推荐验证顺序

1. 先跑内容链路 dry-run：

```powershell
python .\06-工具\scripts\xhs_pipeline.py run --topic-file ".\01-选题管理\01-待深化\2026-02-20-优质客户是聊出来的不是筛出来的.md" --account A --dry-run
```

2. 再跑发布 prepare dry-run：

```powershell
python .\06-工具\scripts\publish_action_runner.py prepare --platform xhs --account A --content ".\02-内容生产\小红书\生成内容\YYYY-MM-DD\xhs-a-YYYYMMDD-标题.md" --dry-run
```

3. 最后再跑 approve：

```powershell
python .\06-工具\scripts\publish_action_runner.py approve --platform xhs --task-id <prepare返回的task_id> --dry-run
```

## 当前机器状态

本机刚做过一次检查：

- 当前没有检测到 BitBrowser 进程
- 默认本地 API `http://127.0.0.1:54345` 超时
- 当前 shell 里没有任何 `BITBROWSER_*` / `XHS_*` 环境变量

所以现在离真实发布还差两步：

1. 启动 BitBrowser 并拿到账号级 `profile_id`
2. 把这些变量写进当前终端或启动脚本
