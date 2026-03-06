# XiaohongshuSkills 借鉴接入安全审计（一期）

## 结论摘要
本次采用“适配层 + 安全包裹”方式接入，不直接修改上游仓库。  
默认策略为安全优先，且默认关闭功能开关，避免影响现有主流程。

## 风险与整改映射
1. 高风险：账号名未严格校验（路径穿越风险）  
- 整改：`validate_account_id()` 强制正则 `[a-zA-Z0-9_-]{1,64}`。

2. 高风险：profile 删除缺 root-jail（误删风险）  
- 整改：`safe_join()` + `ensure_safe_delete()`，禁止删根目录、禁止删 profile_root 本身、禁止越界。

3. 高风险：媒体下载/URL 请求缺 SSRF 防护  
- 整改：`validate_url()` / `validate_redirect_chain()`，拒绝私网、环回、链路本地、保留地址、元数据地址。

4. 中风险：远程 CDP 暴露  
- 整改：默认禁用远程，仅允许 `127.0.0.1`；启用远程时必须在 allowlist。

5. 中风险：依赖版本区间宽松  
- 整改：新增 `requirements.xhs-adapter.lock.txt`，固定版本用于可复现部署。

6. 中风险：日志泄露敏感信息  
- 整改：`redact_text()` 和 URL query 脱敏逻辑，隐藏 token/cookie/auth。

7. 中风险：runner 命令来源可被环境污染  
- 整改：`allowed_binaries` + `allowed_executable_roots` 双重约束。

8. 低风险：异常退出后锁残留导致可用性问题  
- 整改：锁支持 TTL + PID 存活检测，自动回收陈旧锁。

## 当前修复状态
- 账号校验：已实现
- 路径 jail：已实现
- 删除保护：已实现（提供安全检查函数）
- SSRF 过滤：已实现（适配层边界）
- 远程 CDP 限制：已实现
- 依赖锁定：已提供 lock 文件
- 日志脱敏：已实现
- runner 命令白名单：已实现
- 陈旧锁回收：已实现

## 残余风险
1. 适配层对外部 runner 的执行安全依赖命令配置本身，请确保命令来源可信。
2. 若未来接入多机部署，需将锁从本地文件升级为集中式锁。
3. 若外部 runner 内部再次发起网络请求，需在 runner 侧复用同等 URL 安全校验。
