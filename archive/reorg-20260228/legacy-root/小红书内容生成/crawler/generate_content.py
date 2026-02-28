"""
小红书内容生成器 - 完整版
生成文案 + 8张配图，保存到独立文件夹
基于Nano Banana Pro提示词模板库优化
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from gemini_api_generator import GeminiAPIGenerator


# ========== 内容模板 ==========

def get_content_template(topic: str, account: str = "A"):
    """获取内容模板"""

    if account == "A":
        account_name = "转化号"
        color = "#7C3AED"
        badge = "诊断"
    elif account == "B":
        account_name = "交付号"
        color = "#6B7280"
        badge = "SOP"
    else:
        account_name = "观点号"
        color = "#374151"
        badge = "观点"

    return {
        "账号": f"{account}号（{account_name}）",
        "主题": topic,
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "标题候选": [
            f"{topic}太强了！1天写8条脚本不是梦",
            f"{topic}实测｜做内容再也不用想破头",
            f"用了这个{topic}，内容效率翻3倍",
            f"{topic}哪个好？亲测这套最省心",
            f"救命！这个{topic}让我日更不再焦虑"
        ],

        "正文": f"""是不是每天都在经历这些？

选题想破头也没灵感
写完一看全是废话
发了半天没人看

为什么内容越做越累？

❶ 没有选题系统
每天临时想选题，灵感用完就卡壳

❷ 没有内容模板
从零开始写，效率低到爆炸

❸ 没有质检标准
发出去才发现漏洞百出

其实不是你不行，是流程不对

内容交付5步流程：

✅ 定位诊断
先搞清楚你的账号到底要什么

✅ 选题库搭建
30天选题提前锁定，不再临时抱佛脚

✅ 内容日历排期
每天发什么一目了然

✅ 脚本批量生成
AI帮你1天出8条高质量脚本

✅ 质检发布
10条清单保证内容不翻车

💡 今日要点回顾：

✅ 建立选题系统，告别临时想选题
✅ 使用内容模板，提升创作效率
✅ 执行质检清单，保证内容质量

---

你在内容创作中遇到的最大困扰是什么？
欢迎评论区分享你的经验～""",

        "标签": [
            topic,
            "内容创作效率",
            "小红书运营",
            "AI写作",
            "脚本生成",
            "内容日历",
            "自媒体运营",
            "效率工具",
            "内容中台",
            "日更不焦虑"
        ],

        "视觉配置": {
            "点缀色": color,
            "角标": badge,
        }
    }


# ========== 图片提示词 ==========

def get_image_prompts(topic: str, color: str = "#7C3AED"):
    """
    获取8张图的提示词
    基于Nano Banana Pro提示词模板库优化 - 简洁高效版
    """

    return [
        # P1 - 封面
        {
            "name": "01-封面",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a modern tech infographic cover for Chinese social media.

Background: Gradient from deep purple ({color}) to soft lavender (#E9D5FF). Subtle hexagonal grid pattern.

Center: Large 3D white number "8" with purple shadow and glow. Floating icons around it: calendar, lightbulb, checkmark.

Text:
- Top badge: "{topic}" in white pill
- Middle: "AI帮你日更不用愁" bold white Chinese text
- Bottom: "1天8条高质量脚本" with yellow (#FBBF24) highlight on "8条"

Style: Modern, clean, high contrast, premium tech aesthetic.

Negative prompts: blurry, watermark, human faces, cluttered."""
        },

        # P2 - 共鸣场景
        {
            "name": "02-共鸣场景",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a clean infographic card for Chinese social media.

Background: Soft off-white (#FAFAFA) with subtle paper texture.

Layout:
- Top title: "是不是每天都在经历这些？" in bold dark gray, centered
- Three white cards stacked vertically:
  1. "选题想破头也没灵感" with left purple ({color}) border
  2. "写完一看全是废话" with left purple border
  3. "发了半天没人看" with left purple border

Each card has soft shadow, rounded corners, emoji icon on left.

Style: Clean business infographic, professional, high readability.

Negative prompts: blurry, cluttered, 3D effects, photos, watermark."""
        },

        # P3 - 问题本质
        {
            "name": "03-问题本质",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create an infographic showing problem analysis for Chinese social media.

Background: Clean off-white (#FAFAFA).

Layout:
- Top title: "为什么内容越做越累？" in bold dark gray
- Center: Inverted pyramid diagram with 3 layers:
  Layer 1 (top, widest): "没有选题系统" - light purple fill
  Layer 2 (middle): "没有内容模板" - medium purple fill
  Layer 3 (bottom): "没有质检标准" - solid purple ({color})

White Chinese text on each layer, clean edges.

Style: Modern business diagram, clear hierarchy, educational.

Negative prompts: blurry, complex, photos, cluttered, watermark."""
        },

        # P4 - 解决框架
        {
            "name": "04-解决框架",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a vertical flowchart infographic for Chinese social media.

Background: Clean white to light gray gradient.

Layout:
- Top title: "内容交付5步流程" in bold dark text
- Vertical timeline with 5 connected nodes:
  1. Purple circle "1" → "定位诊断"
  2. Purple circle "2" → "选题库搭建"
  3. Purple circle "3" → "内容日历排期"
  4. Purple circle "4" → "脚本批量生成"
  5. Purple circle "5" → "质检发布"

Dotted line connectors between nodes. Small icons next to each step.

Style: Clean process diagram, professional business aesthetic.

Negative prompts: blurry, complex, photos, cluttered, watermark."""
        },

        # P5 - 清单模板
        {
            "name": "05-清单模板",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a checklist infographic for Chinese social media.

Background: Light cream (#FAFAFA) with subtle lined paper texture.

Layout:
- Top title: "脚本质检清单" with purple ({color}) underline
- 8-item checklist in 2 columns:
  Left: ☑开头3秒有钩子 ☑痛点场景具体 ☑解决方案清晰 ☑有数据或案例
  Right: ☑口播节奏自然 ☑画面切换流畅 ☑CTA明确可执行 ☑无敏感词违规

Purple ({color}) checkbox icons, dark gray text, neat rows.

Style: Practical checklist, notebook aesthetic, high utility.

Negative prompts: blurry, 3D effects, photos, watermark."""
        },

        # P6 - 示例展示
        {
            "name": "06-示例展示",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a mockup showcase infographic for Chinese social media.

Background: Soft gradient from light purple (#F3E8FF) to white.

Layout:
- Top title: "实际效果展示" in bold dark text
- Three smartphone mockups arranged horizontally:
  Phone 1: "诊断报告" interface
  Phone 2 (center, larger): "内容日历" with calendar
  Phone 3: "脚本模板" with text layout

Dark gray device frames, soft shadows. Purple ({color}) labels below each phone.

Style: Product showcase, tech demo aesthetic, professional.

Negative prompts: blurry, low quality, realistic photos, cluttered, watermark."""
        },

        # P7 - 要点总结
        {
            "name": "07-要点总结",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a summary infographic for Chinese social media.

Background: Clean white with subtle geometric pattern.

Layout:
- Top title: "3个关键要点" in bold dark text
- Three summary cards stacked vertically:
  Card 1: Checkmark icon + "建立选题系统" + "告别临时想选题"
  Card 2: Checkmark icon + "使用内容模板" + "提升创作效率"
  Card 3: Checkmark icon + "执行质检清单" + "保证内容质量"

White cards with left purple ({color}) border, soft shadows. Clean design.

Style: Educational summary, professional, NO CTA elements, NO action requests.

Negative prompts: CTA buttons, promotional text, "评论", "关注", rewards, giveaways, watermark."""
        },

        # P8 - 互动提问
        {
            "name": "08-互动提问",
            "prompt": f"""Aspect ratio 3:4 vertical (1080x1440px).

Create a closing question card for Chinese social media.

Background: Soft gradient from light purple ({color}) at top to white at bottom.

Layout:
- Center area: Large friendly thinking emoji or question mark icon
- Main text: "你遇到的最大困扰是什么？" in bold dark text, centered
- Subtitle: "欢迎评论区分享你的经验～" in lighter gray text below

Warm, inviting, conversational design. Clean and simple.

Style: Friendly discussion prompt, NOT promotional, NO rewards.

Negative prompts: rewards, giveaways, "领取", "免费", "送", CTA buttons, promotional text, watermark."""
        },
    ]


# ========== 主函数 ==========

async def generate_content(topic: str, account: str = "A"):
    """
    生成完整内容（文案 + 8张图）

    Args:
        topic: 主题/关键词
        account: 账号 (A/B/C)
    """

    print("=" * 60)
    print(f"生成内容: {topic}")
    print(f"账号: {account}号")
    print("=" * 60)

    # 1. 创建输出文件夹
    date_str = datetime.now().strftime("%Y-%m-%d")
    folder_name = f"{date_str}_{account}号_{topic}"
    output_dir = Path(__file__).parent.parent / "生成内容" / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n输出目录: {output_dir}")

    # 2. 生成文案内容
    print("\n[1/2] 生成文案...")
    content = get_content_template(topic, account)

    # 保存JSON
    json_file = output_dir / "内容.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(content, f, ensure_ascii=False, indent=2)

    # 保存纯文本（方便复制）
    txt_file = output_dir / "发布文案.txt"
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(f"标题（5选1）：\n")
        for i, title in enumerate(content['标题候选'], 1):
            f.write(f"{i}. {title}\n")
        f.write(f"\n{'='*40}\n\n")
        f.write(f"正文：\n{content['正文']}\n")
        f.write(f"\n{'='*40}\n\n")
        f.write(f"标签：\n{' '.join(['#' + t for t in content['标签']])}\n")

    print(f"  文案已保存: {txt_file.name}")

    # 3. 生成8张图
    print("\n[2/2] 生成配图...")
    color = content['视觉配置']['点缀色']
    prompts = get_image_prompts(topic, color)

    generator = GeminiAPIGenerator(model="pro")
    await generator.start()

    image_paths = []
    for i, item in enumerate(prompts, 1):
        print(f"  生成 {i}/8: {item['name']}...")
        try:
            # 直接保存到输出目录
            result = await generator.generate_image(
                prompt=item["prompt"],
                account=account,
                page_type=item["name"]
            )

            if result:
                # 移动/复制到输出目录
                src_path = Path(result)
                dst_path = output_dir / f"{item['name']}.png"

                # 如果源文件存在，复制到目标目录
                if src_path.exists():
                    import shutil
                    shutil.copy(src_path, dst_path)
                    image_paths.append(str(dst_path))
                    print(f"    [OK] {dst_path.name}")
                else:
                    print(f"    [WARN] 源文件不存在")
            else:
                print(f"    [FAIL] 生成失败")

        except Exception as e:
            print(f"    [ERROR] {e}")

        # 间隔
        if i < len(prompts):
            await asyncio.sleep(3)

    await generator.close()

    # 4. 更新发布内容JSON（包含图片路径）
    publish_json = output_dir / "发布内容.json"
    publish_data = {
        "title": content['标题候选'][0],
        "content": content['正文'],
        "images": image_paths,
        "tags": content['标签'],
    }
    with open(publish_json, 'w', encoding='utf-8') as f:
        json.dump(publish_data, f, ensure_ascii=False, indent=2)

    # 5. 汇总
    print("\n" + "=" * 60)
    print("生成完成！")
    print("=" * 60)
    print(f"\n文件夹: {output_dir}")
    print(f"\n包含文件:")
    for f in sorted(output_dir.iterdir()):
        print(f"  - {f.name}")

    return str(output_dir)


# ========== 入口 ==========

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小红书内容生成器")
    parser.add_argument("-t", "--topic", type=str, default="AI选题工具", help="主题/关键词")
    parser.add_argument("-a", "--account", type=str, default="A", choices=["A", "B", "C"], help="账号")

    args = parser.parse_args()

    asyncio.run(generate_content(args.topic, args.account))
