---
name: wechat-batch
description: Batch-generate 4 WeChat public account (公众号) long-form articles for this repo's account matrix (gongchang/ipgc/zengzhang/shizhan). Use when you need a strict multi-file delivery contract (FILES_JSON + FILE blocks) so content can be auto-saved into 生成内容/YYYY-MM-DD/*.md, with optional 金句库 and 富贵「打动人模块」 injection.
---

# 公众号四账号批量爆文生产线（WeChat）

目标：一次 Brief，产出 4 个账号的文章，并按“可落盘合同”交付。

账号与风格（固定）：
- `gongchang`：系统方法/对比/框架，偏理性但不冰冷
- `ipgc`：情绪共鸣/年龄阶段/反转，偏共情
- `zengzhang`：数据驱动/增长视角/指标语言，偏硬核
- `shizhan`：轻松实用/行动清单/避坑，偏落地

## 快速开始（输入 Brief）

优先让用户按这个结构给你 Brief（字段缺失就先补问，别猜）：

```text
日期(YYYY-MM-DD，可空，默认今天)：
主题矿区/选题：
目标人群：
核心矛盾：
场景证据/案例素材：
希望读者做的第一步：
引流物(CTA关键词)：
禁区(可选)：不聊什么/不碰什么
是否调用金句库：是|否
金句主题：搞钱与生意/内容与增长/系统与执行/人性与沟通
```

## 工作流（标题 -> 大纲 -> 正文 -> 打包落盘）

### Step 0：加载上下文（按需）

必选（如果路径存在）：
- `公众号内容生成器/公众号内容生成器_完整版Skill.md`

可选（按需要增强标题/正文生产线）：
- `公众号爆文写作系统（增强版：标题库驱动 + 正文生产线）.md`
- `公众号内容生成器/1.input/内容框架知识库.md`
- `公众号内容生成器/1.input/选题素材库.md`
- `公众号内容生成器/1.input/情绪价值点库.md`
- `公众号内容生成器/1.input/认知框架库.md`
- `公众号内容生成器/templates/内容框架模板.md`
- `公众号内容生成器/templates/视觉风格库.md`
- `公众号内容生成器/resources/故事素材库.md`

可选（仅当 Brief 明确允许）：
- 金句库：`金句库/00-索引.md` + 你选的主题文件（例如 `金句库/01-搞钱与生意.md`）
- 富贵模块：`富贵-打动人模块.md`（用于“具体的人”“事实/感受/想象”改写）

金句库硬约束：
- 只有当 Brief 写了 `是否调用金句库：是` 才能引用/改写金句。
- 只允许从已加载进上下文的金句库文件里挑选并改写；不要凭空编造“金句”。

### Step 1：四账号标题（互斥公式）

为每个账号输出：
- `1` 个主标题 + `2` 个备选标题
- 每个账号必须使用不同标题公式；四个主标题前 4 个字不能相同
- 标题尽量 18-28 字，少标点，不抄“爆款原句”

### Step 2：四账号大纲（开头四件套 + 01/02/03 闭环）

每篇大纲包含：
- 开头四件套：身份锚点/场景细节/扎心矛盾/钩子承诺
- 01/02/03：每点至少含 观点/案例细节/底层逻辑/可执行第一步/反驳安全阀/（可选）金句
- 结尾 CTA：承接 Brief 的引流物关键词

### Step 3：四账号正文草稿（可完稿）

硬约束（每篇都要满足）：
- 字数 1500-3000（允许浮动，但要信息密度）
- 排版：每 2-4 行换行
- 至少 1 句可截图金句（加粗）
- 至少 2 处“反驳安全阀”（避免极端与抬杠）
- 全文 3 处埋点（反常识/数字对比/场景细节等）
- 结尾 CTA 明确，且用 Brief 的引流物关键词

### Step 4：打包落盘（严格输出合同）

你必须输出一个“机器可解析”的交付包；除下述结构外，不要输出任何多余文字。

日期规则：
- 如果 Brief 提供日期就用它；否则用本地今天（YYYY-MM-DD）
- `yyyymmdd` = 去掉日期里的 `-`

路径规则：
- `生成内容/YYYY-MM-DD/{prefix}-{YYYYMMDD}-{短标题}.md`
- `短标题`：从主标题抽取 10-18 个字，去标点与特殊符号（不要求完美，程序会再做文件名清理）

每个 md 文件必须包含：
- YAML frontmatter
- `## 标题`
- `## 正文`
- `CTA`
- `## 配图提示词`：至少 1 张封面 + N 张章节配图（N 随小标题数量）

输出合同（固定标记）：
1. 先输出纯 JSON（只含 `date` + `files[].path`）：
```text
<!--FILES_JSON_START-->
{"date":"YYYY-MM-DD","files":[{"path":"生成内容/YYYY-MM-DD/gongchang-YYYYMMDD-短标题.md"},{"path":"生成内容/YYYY-MM-DD/ipgc-YYYYMMDD-短标题.md"},{"path":"生成内容/YYYY-MM-DD/zengzhang-YYYYMMDD-短标题.md"},{"path":"生成内容/YYYY-MM-DD/shizhan-YYYYMMDD-短标题.md"}]}
<!--FILES_JSON_END-->
```

2. 紧跟着按 JSON 的 `files` 顺序输出 4 个文件块（path 单独一行）：
```text
<!--FILE_START-->
生成内容/YYYY-MM-DD/gongchang-YYYYMMDD-短标题.md
<这里是完整 md 内容>
<!--FILE_END-->
```

重复 4 次，顺序必须与 JSON 一致。

## 可选加分（不破坏合同前提下）

- 如果加载了 `富贵-打动人模块.md`：每篇在开头写“具体的人”场景，并把核心观点至少写出“事实/感受/想象”两层。
- 四篇的开头不要同句式；四篇的 CTA 也不要同句式（但引流关键词必须一致）。

