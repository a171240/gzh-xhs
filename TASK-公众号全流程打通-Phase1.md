# 任务说明：公众号全流程打通 - Phase 1（主干对齐版）

## 1. 文档目的

本任务文档用于把“公众号内容生成 -> 图片生成 -> markdown 回写”这一段流程，改写成基于当前主干仓库结构的可实施说明。

Phase 1 只处理以下三件事：

1. 公众号内容产出时补齐 `summary`
2. 新增可调用的“公众号图片生成” skill
3. 图片生成后自动回写 markdown，并执行压缩

本阶段不改发布链路，不扩展桌面端新向导卡片，不处理公众号后台上传与发表逻辑。

---

## 2. Phase 1 范围与非范围

### 范围内

- 统一公众号内容产物的 frontmatter，新增 `summary`
- 让图片生成支持自然语句和 `/skill` 两种入口
- 为 `wechat_image_generator.py` 增加 markdown 回写与压缩能力
- 保持现有批量日期处理、重试、`index.json`、`run.log` 机制

### 范围外

- 不修改 `06-工具/scripts/wechat_publisher.py`
- 不新增 desktop-app 的技能卡片、wizard schema、wizard prompt
- 不改公众号发布审批、定时发表、素材上传流程
- 不处理摘要与封面图在发布阶段的消费逻辑，留到 Phase 2

---

## 3. 当前基线（以 2026-03-06 主干为准）

### 3.1 内容生产基线

- 核心内容 skill：`skills/自有矩阵/公众号批量生产.md`
- 桌面端公众号技能注册：`06-工具/desktop-app/data/skills.json`
- 桌面端公众号流水线 prompt：`06-工具/desktop-app/data/prompts.default.json`
- 目标落盘目录：`02-内容生产/公众号/生成内容/YYYY-MM-DD/`

### 3.2 图片生成基线

- 图片脚本：`06-工具/scripts/wechat_image_generator.py`
- 当前已具备能力：
  - 解析 `## 配图提示词` section 下的代码块
  - 支持单文件和 `--batch-date`
  - 支持失败重试
  - 输出到 `images/<账号缩写>/`
  - 生成 `index.json` 和 `run.log`
- 当前缺失能力：
  - 不会回写 markdown
  - 不会补 `cover_image`
  - 不会压缩图片
  - 不会生成或更新 `summary`

### 3.3 技能接线基线

- `06-工具/scripts/feishu_skill_runner.py` 当前主要依赖：
  - desktop-app `skills.json` 中的 `defaultContexts`
  - 全局 `SKILL.md` 目录式 skill
- 仅在 `skills/自有矩阵/` 下新增一个 `.md` 文件，当前并不能保证被 runner 识别
- `06-工具/scripts/feishu_kb_orchestrator.py` 当前弱触发规则要求消息中出现“用/使用/skill”一类字样
- `生成公众号图片：2026-03-06` 这种自然语句，当前不能直接命中新 skill

### 3.4 与旧文档的差异

以下旧路径或旧假设不再作为实现依据：

- `公众号内容生成器/...`
- `生成内容/YYYY-MM-DD/...`
- 新发明的 `generation-report.json`
- “新建 skill 文件后即可自动接入”的假设

如需在文档中保留迁移说明，只允许做一次新旧路径对照，不允许继续沿用旧结构描述任务。

---

## 4. Phase 1 完成态

完成后应满足以下结果：

1. 公众号四账号内容落盘到 `02-内容生产/公众号/生成内容/YYYY-MM-DD/`
2. 每个 md 文件 frontmatter 含 `summary`
3. 可以通过自然语句 `生成公众号图片：<日期或md路径>` 触发图片生成
4. 可以通过 `/skill 公众号图片生成 需求: <日期或md路径>` 作为兜底入口
5. `wechat_image_generator.py` 支持：
   - `--insert-to-md`
   - `--no-insert`
   - `--compress`
   - `--no-compress`
   - `--max-size-kb`
6. 图片落盘后自动写回 `cover_image`
7. 正文内插入图片引用，使用相对路径 `images/{abbr}/...`
8. 继续使用现有 `index.json` 与 `run.log` 作为报告与追踪基线

---

## 5. 需要完成的实施项

### 任务1：对齐公众号内容 skill 的最终交付契约

**文件**：`skills/自有矩阵/公众号批量生产.md`

### 修改目标

不要新增“Step 4.3：生成摘要”这种并列步骤。

本任务要求把 `summary` 直接并入现有最终交付契约，使其成为每个 md 文件 frontmatter 的必填字段，而不是额外的后处理说明。

### 需要修改的内容

1. 在 Step 4 的“每个 md 文件必须包含”中，明确 frontmatter 必填字段至少包含：
   - `账号`
   - `日期`
   - `选题`
   - `标题公式`
   - `配图风格`
   - `字数`
   - `summary`
2. 在 Step 4 的产物结构说明中，明确 `summary` 的要求：
   - 基于最终正文生成，而不是仅摘正文前 200 字
   - 长度控制在 50-120 字
   - 保留核心观点、价值承诺、行动方向
   - 不写标题党，不重复主标题，不用 markdown 标记
   - 作为单行字符串写入 frontmatter：`summary: "..."`
3. 在质检清单中新增：
   - [ ] 每个 md 文件 frontmatter 含 `summary`
   - [ ] `summary` 与正文一致，无夸大承诺
4. 修正所有旧路径表达，统一使用：
   - `02-内容生产/公众号/生成内容/YYYY-MM-DD/...`

### 结果要求

- skill 文档本身必须把 `summary` 作为输出契约的一部分写死
- 后续执行 skill 时，不需要额外询问是否生成摘要

---

### 任务2：同步桌面端公众号流水线 prompt 契约

**文件**：`06-工具/desktop-app/data/prompts.default.json`

**目标 stage**：`wechat_step4`

### 修改目标

让桌面端流水线的最终交付 prompt 与 `skills/自有矩阵/公众号批量生产.md` 保持一致，避免 skill 文档和真正执行 prompt 输出两套不同契约。

### 需要修改的内容

1. 修正 `wechat_step4` 中的文件路径规则：
   - 旧写法：`生成内容/YYYY-MM-DD/...`
   - 新写法：`02-内容生产/公众号/生成内容/YYYY-MM-DD/...`
2. 明确每个 md 文件的必备结构：
   - YAML frontmatter
   - `## 标题`
   - `## 正文`
   - `CTA`
   - `## 配图提示词`
3. 在 frontmatter 要求中补入 `summary`
4. 保留 `FILES_JSON_START / FILE_START / FILE_END` 解析合同，不改变标记名
5. 不扩展 desktop-app 新卡片，只修改现有 `wechat` 流水线的最终输出约束

### 结果要求

- 桌面端执行“写公众号”时，最终 md 能直接满足 Phase 1 的摘要与配图前置条件
- skill 文档与 prompt 契约对同一件事的描述不能冲突

---

### 任务3：新增“公众号图片生成” skill 说明

**文件**：`skills/自有矩阵/公众号图片生成.md`（新建）

### 目标

定义一个独立图片 skill，专门承接“对已生成公众号 markdown 执行图片生成 + markdown 回写”这条链路。

### 输入契约

支持两类输入：

1. 日期：

```text
生成公众号图片：2026-03-06
```

2. 单个 md 路径：

```text
生成公众号图片：02-内容生产/公众号/生成内容/2026-03-06/gongchang-20260306-示例标题.md
```

兜底入口：

```text
/skill 公众号图片生成 需求: 2026-03-06
```

或

```text
/skill 公众号图片生成 需求: 02-内容生产/公众号/生成内容/2026-03-06/gongchang-20260306-示例标题.md
```

### Skill 内必须说明的依赖

- 依赖现有 markdown 文件中的 `## 配图提示词` section
- 依赖 `## 配图提示词` 下使用代码块承载 prompt
- 如果缺少 `## 配图提示词` 或没有可解析 prompt，必须报错并停止该文件处理

### Skill 内必须说明的执行行为

1. 输入为日期时：
   - 查找 `02-内容生产/公众号/生成内容/<日期>/`
   - 若目录不存在，再回退检查旧兼容目录 `06-工具/生成内容/<日期>/`
2. 输入为单文件路径时：
   - 直接处理该 md 文件
3. 调用脚本：
   - 批量模式：`python 06-工具/scripts/wechat_image_generator.py --batch-date <日期> --insert-to-md --compress`
   - 单文件模式：`python 06-工具/scripts/wechat_image_generator.py <md文件> --insert-to-md --compress`
4. 输出结果汇总：
   - 成功文件数
   - 每篇生成的封面/配图数量
   - 已更新的 markdown 文件
   - 失败文件及失败原因
   - `index.json` / `run.log` 路径

### Skill 内必须说明的错误场景

- 日期目录不存在
- md 文件不存在
- 缺少 `## 配图提示词`
- prompt 解析为空
- 图片生成重试后仍失败
- markdown 回写失败
- 压缩后仍超出阈值

---

### 任务4：补齐图片 skill 接线与脚本能力

本任务不是“只新建一个 markdown skill 文件”，而是把它写成真实可调用能力。

### 4.1 修改 skill runner，让新 skill 可被解析

**文件**：`06-工具/scripts/feishu_skill_runner.py`

### 需要说明的改动

1. 让 `skills/自有矩阵/公众号图片生成.md` 可以被 runner 识别为可调用 skill
2. 为该 skill 提供稳定的 canonical id 与别名映射，至少支持：
   - `公众号图片生成`
   - `/skill 公众号图片生成`
   - 必要时的英文/内部 id
3. 为该 skill 明确默认平台为 `公众号`
4. 为该 skill 增加“执行型 skill”落盘策略：
   - 允许 runner 执行 skill，但不为该 skill 额外生成新的 markdown 结果文件
   - runner 返回执行摘要与错误信息即可
5. 本阶段不要求把它接入 desktop-app `skills.json`

### 4.2 修改编排器，让自然语句可直接触发

**文件**：`06-工具/scripts/feishu_kb_orchestrator.py`

### 需要说明的改动

1. 增加自然语句意图识别，至少支持以下模式：
   - `生成公众号图片：2026-03-06`
   - `生成公众号图片: 2026-03-06`
   - `生成公众号图片：<md路径>`
2. 自然语句命中后，应解析为图片 skill，而不是落到 plain chat fallback
3. `/skill 公众号图片生成 需求: ...` 仍保留为强触发兜底入口

### 4.3 修改图片脚本，补齐 markdown 回写与压缩

**文件**：`06-工具/scripts/wechat_image_generator.py`

### 新增命令行参数

```python
--insert-to-md
--no-insert
--compress
--no-compress
--max-size-kb
```

### 脚本行为要求

#### A. markdown 回写

图片生成成功后：

1. 读取原 markdown
2. 解析 frontmatter 与 `## 正文`
3. 写入或更新：
   - `cover_image`
4. 在正文中插入图片引用：
   - 使用相对路径 `images/{abbr}/...`
   - 仅在段落之间插入
   - 优先在小标题后插入
   - 避免插入到代码块或段落内部
5. 保存更新后的 markdown

`cover_image` 写入规则：

- 若封面实际文件仍为 PNG，则写：

```yaml
cover_image: "images/{abbr}/cover.png"
```

- 若压缩后转为 JPG，则必须写入实际文件名，不允许 frontmatter 与真实文件路径不一致

#### B. 图片压缩

1. 默认压缩开关开启，可通过 `--no-compress` 关闭
2. 默认阈值 500KB，可通过 `--max-size-kb` 覆盖
3. 压缩后必须同步更新：
   - markdown 中插图路径
   - frontmatter 的 `cover_image`
   - `index.json` 中的 `output` 路径
4. 保留现有 `run.log`
5. 不再新增额外的 `generation-report.json`

#### C. 保持现有能力不被破坏

- 保留单文件模式
- 保留 `--batch-date`
- 保留重试逻辑
- 保留 `images/<abbr>/index.json`
- 保留旧目录兼容回退

---

## 6. 前后结构对齐要求

实现时必须满足以下上下游对齐关系：

### 内容 skill 输出 -> 图片 skill 输入

- 内容 md 必须包含 `## 配图提示词`
- frontmatter 必须包含 `summary`

### 图片脚本输出 -> 发布侧未来消费

- 图片脚本必须写入 `cover_image`
- Phase 1 不修改 `wechat_publisher.py`
- Phase 2 可直接复用 `summary` 与 `cover_image`

---

## 7. 测试计划

### 测试1：内容生成产物检查

### 输入

执行现有“写公众号”流程，生成某一日期的四账号内容。

### 预期

- 4 个 md 文件落在 `02-内容生产/公众号/生成内容/YYYY-MM-DD/`
- 每个 md frontmatter 包含 `summary`
- 每个 md 包含 `## 配图提示词`

---

### 测试2：自然语句触发图片 skill

### 输入

```text
生成公众号图片：2026-03-06
```

### 预期

- 编排器将该消息识别为图片 skill，而不是普通聊天
- 调用批量图片生成脚本
- 成功文件生成封面图和章节配图

---

### 测试3：`/skill` 兜底入口

### 输入

```text
/skill 公众号图片生成 需求: 2026-03-06
```

### 预期

- runner 可以解析该 skill
- 批量模式执行成功

---

### 测试4：单文件图片生成

### 输入

```text
生成公众号图片：02-内容生产/公众号/生成内容/2026-03-06/gongchang-20260306-示例标题.md
```

### 预期

- 只处理该单个文件
- 生成 `images/gongchang/cover.*` 与 `img-NN.*`
- 写回该 md 文件的 `cover_image` 和正文插图

---

### 测试5：脚本参数验证

### 输入

```bash
python 06-工具/scripts/wechat_image_generator.py --batch-date 2026-03-06 --insert-to-md --compress
```

### 预期

- 生成 `cover.png` / `img-NN.png`，或在压缩转换后生成对应的实际扩展名文件
- markdown 中插入实际图片路径
- frontmatter 含 `cover_image`
- `index.json` 与真实产物路径一致

---

### 测试6：错误场景

### 场景A：日期目录不存在

输入：

```text
生成公众号图片：2026-01-01
```

预期：

- 明确提示找不到日期目录

### 场景B：缺少 `## 配图提示词`

操作：

- 手动删除某个 md 的 `## 配图提示词` section
- 再执行图片生成

预期：

- 报错指出该文件缺少 `## 配图提示词`

### 场景C：图片生成失败

预期：

- 按现有重试策略重试
- 最终失败项写入汇总输出和 `run.log`

### 场景D：压缩未达标

预期：

- 返回明确错误或警告
- 不允许静默假成功

---

## 8. 验收标准

- [ ] 公众号内容 skill 最终交付契约已纳入 `summary`
- [ ] desktop-app `wechat_step4` 契约与 skill 文档一致
- [ ] 已新增 `skills/自有矩阵/公众号图片生成.md`
- [ ] 新图片 skill 可通过 runner 解析
- [ ] 新图片 skill 不会额外落盘无关 markdown 文件
- [ ] `生成公众号图片：<日期或md路径>` 能被编排器识别
- [ ] `/skill 公众号图片生成 需求: ...` 能正常执行
- [ ] `wechat_image_generator.py` 支持 markdown 回写
- [ ] `wechat_image_generator.py` 支持压缩开关与阈值控制
- [ ] 图片实际路径、`cover_image`、正文引用、`index.json` 三者一致
- [ ] 全流程未引入新的自定义报告文件，继续沿用 `index.json` + `run.log`

---

## 9. 重要约束

1. 不要把 Phase 2 内容混入本阶段
   - 不改 `wechat_publisher.py`
   - 不补发布侧摘要消费逻辑

2. 不要只写“新增 markdown skill 文件”
   - 必须把 runner 识别与 orchestrator 触发写成真实任务

3. 不要继续使用旧主干路径描述实现
   - 文档中所有实施路径统一按当前主干书写

4. 不要在脚本侧另造报告格式
   - 继续以 `images/<abbr>/index.json` 和 `run.log` 为基线

---

## 10. 参考文件

- `skills/自有矩阵/公众号批量生产.md`
- `06-工具/desktop-app/data/prompts.default.json`
- `06-工具/scripts/wechat_image_generator.py`
- `06-工具/scripts/README_WECHAT_IMAGE_GENERATOR.md`
- `06-工具/scripts/feishu_skill_runner.py`
- `06-工具/scripts/feishu_kb_orchestrator.py`
- `06-工具/scripts/wechat_publisher.py`（仅供了解下阶段消费方，不在本阶段修改）

---

## 11. 预计工作量

- 任务1：0.5 小时
- 任务2：0.5 小时
- 任务3：0.5 小时
- 任务4：2-4 小时
- 测试与回归：1-2 小时

**总计**：4.5-7.5 小时

---

## 12. 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.0 | 2026-03-06 | 按当前主干仓库结构重写 Phase 1，明确摘要、图片 skill、runner/orchestrator 接线与 markdown 回写边界 |
