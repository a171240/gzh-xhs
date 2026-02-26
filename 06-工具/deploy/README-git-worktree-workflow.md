# Git Worktree 双目录工作流（防止误判“文件被删”）

## 目标
- 固定一个目录用于开发分支。
- 固定一个目录用于发布分支 `main`。
- 切分支前先做删除风险检查，避免把“分支差异”误判成“文件被删”。

## 建议目录
- 开发目录：`E:\公众号内容生成`
- 发布目录：`E:\gzh-xhs-main`

## 一次性初始化
```powershell
cd E:\公众号内容生成
git worktree add E:\gzh-xhs-main main
git worktree list
```

## 日常使用
### 1) 在开发目录改代码
```powershell
cd E:\公众号内容生成
git branch --show-current
```

### 2) 在发布目录维护 main
```powershell
cd E:\gzh-xhs-main
git branch --show-current
git pull --ff-only
```

### 3) 切分支前先跑风险检查
```powershell
cd E:\公众号内容生成
powershell -ExecutionPolicy Bypass -File .\06-工具\deploy\check-switch-risk.ps1 -TargetBranch main
```

如果输出 `Deleted > 0`，脚本会默认阻断。
只有确认“这是预期差异”时，才用 `-Force`。
```powershell
powershell -ExecutionPolicy Bypass -File .\06-工具\deploy\check-switch-risk.ps1 -TargetBranch main -Force
```

## 排错
### 查看所有 worktree
```powershell
git worktree list
```

### 删除不再需要的 worktree
```powershell
git worktree remove E:\gzh-xhs-main
```

### 如果看起来“目录少了”
先确认你当前所在仓库根目录和分支：
```powershell
git rev-parse --show-toplevel
git branch --show-current
```

多数情况不是删除，而是不同分支内容不同。
