# 内容生成控制台（桌面版）

## 快速开始

```bash
cd desktop-app
npm install
npm run dev
```

## 功能

- Electron 桌面壳 + 双栏工作区布局。
- 提示词编排流水线（多阶段，支持 {{input}}/{{context}}/{{prev}}）。
- Markdown/文本上下文文件加载。
- 右侧模板/素材快速筛选面板。
- OpenAI Responses API 接入（Node SDK）。
- 流式输出 + 步骤进度显示。
- 技能选择：公众号爆文写作 / 小红书内容生成。

## 说明

- 在设置里填写 OpenAI API 密钥（只保存在本机用户目录）。
- 流水线配置同样保存在本机用户目录，可在界面中直接修改。
- 默认模型是 `gpt-4.1-mini`，也可以改为其它模型字符串。
