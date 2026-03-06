# wechat_layout_compiler

这是仓库内正式受管的 Node 子项目，用于生成公众号排版预览与渲染产物。

## 纳管范围
- `package.json`
- `package-lock.json`
- `render.js`
- 本 README

## 不纳管内容
- `node_modules/`
- 本地 npm 缓存

`node_modules/` 继续通过仓库 `.gitignore` 和本目录 `.gitignore` 忽略，不应作为源码提交。

## 安装与重建
```bash
cd 06-工具/scripts/wechat_layout_compiler
npm ci
```

若只是本地试验性增加依赖，再使用 `npm install`；正式纳管前必须回写 `package-lock.json`。

## 运行
```bash
cd 06-工具/scripts/wechat_layout_compiler
npm run render
```

## 约定
- 依赖版本以 `package-lock.json` 为准
- 本地删除 `node_modules/` 后，允许按锁文件重建
- 若排版编译器升级依赖，必须同时更新锁文件
