# 小红书图片生成器

执行脚本：`06-工具/scripts/xhs_image_generator.py`

输入要求：

- 只接受 canonical 小红书内容文件。
- 必须包含 `## 配图提示词`，且先经过 `xhs_prompt_normalizer.py` 标准化。
- frontmatter 里的 `account`、`mode`、`chosen_title` 不能为空。

执行约束：

- 信息图模式固定生成 `cover + p1..p6`。
- 情绪帖模式固定生成 `cover + body-01..body-03`，实际可少于 4 张。
- 真相源只有 sidecar manifest：`<content-stem>.images.json`。

输出：

- 图片文件写入 `内容同级/images/<account_prefix>/`
- sidecar manifest 写入 `<content-stem>.images.json`
- 回写内容 frontmatter：
  - `image_manifest`
  - `publish_ready`
