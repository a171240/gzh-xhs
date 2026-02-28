# Gemini图片生成器 - 使用说明

## 功能概述

使用Playwright自动化操作Gemini网页，批量生成小红书8张轮播图。

## 前置条件

1. **安装依赖**
```bash
cd 小红书内容生成/crawler
pip install -r requirements.txt
playwright install chromium
```

2. **登录Gemini**
   - 在Chrome浏览器中打开 https://gemini.google.com
   - 完成Google账号登录
   - 确保可以正常使用Gemini的图片生成功能

3. **关闭Chrome**
   - 运行脚本前，需要关闭所有Chrome窗口
   - Playwright需要独占访问Chrome的用户数据目录

## 使用方法

### 方式1：测试模式（生成单张图）

```bash
python run_image_generator.py --test
```

### 方式2：生成完整视觉包（8张图）

```bash
# 生成A号（转化号）视觉包
python run_image_generator.py -a A

# 生成B号（交付号）视觉包
python run_image_generator.py -a B

# 生成C号（观点号）视觉包
python run_image_generator.py -a C
```

### 方式3：指定主题

```bash
python run_image_generator.py -a A -t "AI脚本生成"
```

### 方式4：只生成部分页面

```bash
# 只生成封面和P2
python run_image_generator.py -a A -p 封面 P2

# 只生成P3-P5
python run_image_generator.py -a A -p P3 P4 P5
```

## 输出目录

生成的图片保存在：
```
小红书内容生成/
└── generated_images/
    └── 2026-01-11/
        ├── A-封面-143052.png
        ├── A-P2-143125.png
        ├── A-P3-143158.png
        └── ...
```

## 8张图说明

| 页面 | 内容 | 文案要求 |
|------|------|----------|
| 封面 | 痛点+结果+主关键词 | ≤18字强钩子 |
| P2 | 共鸣场景 | 标题+3点 |
| P3 | 问题本质/机制 | 标题+3点 |
| P4 | 解决框架（5步流程） | 每节点≤8字 |
| P5 | 模板/清单 | 标题+要点 |
| P6 | 示例截图 | 说明用哪张真实截图 |
| P7 | 行动步骤（3步） | 落地执行 |
| P8 | CTA页 | 评论关键词/私信关键词 |

## 账号视觉区分

| 账号 | 色调 | 角标 |
|------|------|------|
| A号（转化） | 紫色/主色强调 | 「诊断」 |
| B号（交付） | 灰色调 | 「SOP」 |
| C号（观点） | 黑灰调 | 「观点」 |

## 常见问题

### Q: 提示"无法复用Chrome Profile"
A: 请确保已关闭所有Chrome窗口后再运行脚本

### Q: 图片生成超时
A: Gemini生成图片需要时间，脚本会等待最长90秒。如果频繁超时，可能是网络问题

### Q: 登录状态失效
A: 重新在Chrome中登录Gemini，然后关闭Chrome再运行脚本

### Q: 生成的图片质量不满意
A: 可以手动修改 `run_image_generator.py` 中的提示词模板

## 注意事项

1. **Gemini限制**：免费版可能有图片生成次数限制
2. **间隔时间**：每张图生成后会等待3秒，避免请求过快
3. **后期处理**：AI生成的底图需要后期添加文字和角标
4. **P6页面**：需要手动添加真实截图

## 与Skill工作流集成

在7步工作流的Step 5.5后，可选执行Step 5.6：

```
Step 5.5: 生成视觉包（8张图提示词+每页文案清单）
Step 5.6: 自动生成图片（可选）
    ├── 读取Step 5.5生成的8张图提示词
    ├── 运行 python run_image_generator.py
    └── 图片保存到 generated_images/
```
