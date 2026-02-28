"""
小红书视觉包批量生成脚本
使用Playwright连接到已打开的Chrome（可手动选择Nano Banana Pro模型）

使用方法：
1. 启动Chrome: start chrome --remote-debugging-port=9222
2. 在Chrome中打开 gemini.google.com，选择 Nano Banana Pro 模型
3. 运行: python run_image_generator.py
"""

import asyncio
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List

# 优先使用Playwright方式（可以手动选择模型）
# 如果CDP连接失败，则回退到API方式
try:
    from gemini_image_generator import GeminiImageGenerator
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    from gemini_api_generator import GeminiAPIGenerator
    API_AVAILABLE = True
except ImportError:
    API_AVAILABLE = False

# 默认使用API方式（Gemini 3.0 Pro模型）
USE_PLAYWRIGHT = False


# ========== 账号视觉配置 ==========

ACCOUNT_STYLES = {
    "A": {
        "name": "转化号",
        "color": "紫色(#7C3AED)",
        "color_hex": "#7C3AED",
        "badge": "诊断",
        "cta_keywords": ["诊断", "演示", "模板"]
    },
    "B": {
        "name": "交付号",
        "color": "灰色(#6B7280)",
        "color_hex": "#6B7280",
        "badge": "SOP",
        "cta_keywords": ["SOP", "质检", "归档", "协作"]
    },
    "C": {
        "name": "观点号",
        "color": "深灰(#374151)",
        "color_hex": "#374151",
        "badge": "观点",
        "cta_keywords": ["诊断", "质检"]
    }
}


# ========== 内容数据结构 ==========

@dataclass
class VisualPackContent:
    """视觉包内容数据"""
    # 封面
    main_title: str = "AI帮你日更不用愁"  # ≤18字
    sub_title: str = "1天8条高质量脚本"   # ≤10字

    # P2 共鸣场景
    p2_title: str = "是不是每天都在经历这些？"
    pain_points: List[str] = field(default_factory=lambda: [
        "选题想破头也没灵感",
        "写完一看全是废话",
        "发了半天没人看"
    ])

    # P3 问题机制
    p3_title: str = "为什么内容越做越累？"
    reasons: List[str] = field(default_factory=lambda: [
        "没有选题系统",
        "没有内容模板",
        "没有质检标准"
    ])

    # P4 解决框架（5步）
    p4_title: str = "内容交付5步流程"
    steps_5: List[str] = field(default_factory=lambda: [
        "定位诊断",
        "选题库搭建",
        "内容日历排期",
        "脚本批量生成",
        "质检发布"
    ])

    # P5 清单
    p5_title: str = "脚本质检清单"
    checklist: List[str] = field(default_factory=lambda: [
        "开头3秒有钩子",
        "痛点场景具体",
        "解决方案清晰",
        "有数据或案例",
        "口播节奏自然",
        "画面切换流畅",
        "CTA明确可执行",
        "无敏感词违规"
    ])

    # P6 示例截图
    p6_title: str = "实际效果展示"
    screenshot_labels: List[str] = field(default_factory=lambda: [
        "诊断报告",
        "内容日历",
        "脚本模板"
    ])

    # P7 执行步骤（3步）
    p7_title: str = "立刻执行这3步"
    actions: List[tuple] = field(default_factory=lambda: [
        ("评论【诊断】", "获取8题自测问卷"),
        ("完成问卷", "5分钟找到内容卡点"),
        ("领取30天行动清单", "按清单执行即可")
    ])

    # P8 CTA
    cta_action: str = "评论【诊断】"
    cta_items: List[str] = field(default_factory=lambda: [
        "8题五维评分",
        "30天行动清单",
        "定制化改造建议"
    ])


# ========== Nano Banana Pro 提示词模板 ==========

def get_visual_prompts(account: str = "A", content: VisualPackContent = None) -> dict:
    """
    获取8张图的完整提示词（Nano Banana Pro格式，直接生成带中文的完成图）

    Args:
        account: 账号类型 (A/B/C)
        content: 内容数据，如果为None则使用默认内容

    Returns:
        8张图的提示词字典
    """
    if content is None:
        content = VisualPackContent()

    style = ACCOUNT_STYLES.get(account, ACCOUNT_STYLES["A"])
    color = style["color"]

    prompts = {
        "封面": f"""【图片类型】小红书轮播图封面
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简奢华杂志封面设计。珍珠白到浅灰渐变背景(#F8F8F8 → #EEEEEE)，大面积留白60%+。

【核心内容】
- 主标题：「{content.main_title}」
  - 位置：画面正中央偏上
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度60-70%
  - 效果：清晰锐利，手机端一眼可读
- 副标题：「{content.sub_title}」
  - 位置：主标题正下方
  - 字体：现代无衬线细体，浅灰色(#666666)
  - 大小：主标题的40-50%

【装饰元素】
- 少量{color}发光线条或光点粒子
- 底部细线装饰
- 整体克制、高级、干净

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文、水印、logo""",

        "P2": f"""【图片类型】小红书轮播图内页-共鸣场景
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务信息图卡片。雾白背景(#FAFAFA)，{color}装饰点缀，留白充足。

【核心内容】
- 页面标题：「{content.p2_title}」
  - 位置：画面顶部，距顶10%
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度50%

- 三个痛点卡片（横向排列）：
  - 卡片1：「{content.pain_points[0]}」
  - 卡片2：「{content.pain_points[1]}」
  - 卡片3：「{content.pain_points[2]}」
  - 位置：画面中部，三个圆角矩形均匀分布
  - 卡片样式：白色卡片(#FFFFFF)，轻微阴影
  - 文字：中灰色(#555555)，居中显示

【装饰元素】
- {color}细线边框或图标
- 卡片间有视觉连接

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文""",

        "P3": f"""【图片类型】小红书轮播图内页-问题机制
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务信息图卡片。雾白背景(#FAFAFA)，{color}装饰点缀，留白充足。

【核心内容】
- 页面标题：「{content.p3_title}」
  - 位置：画面顶部，距顶10%
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度50%

- 机制解释图（漏斗形状）：
  - 层级1：「{content.reasons[0]}」
  - 层级2：「{content.reasons[1]}」
  - 层级3：「{content.reasons[2]}」
  - 位置：画面中部
  - 样式：{color}渐变填充，白色文字

【装饰元素】
- 层级之间有箭头或连接线
- {color}渐变效果

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文""",

        "P4": f"""【图片类型】小红书轮播图内页-解决框架
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务信息图卡片。雾白背景(#FAFAFA)，{color}装饰点缀，留白充足。

【核心内容】
- 页面标题：「{content.p4_title}」
  - 位置：画面顶部，距顶8%
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度50%

- 五步流程图（纵向排列）：
  - Step1：「{content.steps_5[0]}」
  - Step2：「{content.steps_5[1]}」
  - Step3：「{content.steps_5[2]}」
  - Step4：「{content.steps_5[3]}」
  - Step5：「{content.steps_5[4]}」
  - 位置：画面中部，5个节点纵向排列
  - 节点样式：{color}圆形，白色序号
  - 文字：深灰色(#333333)，节点右侧
  - 节点间有箭头连接

【装饰元素】
- 流程箭头使用{color}
- 节点之间等距排列

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文""",

        "P5": f"""【图片类型】小红书轮播图内页-清单模板
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务信息图卡片。雾白背景(#FAFAFA)，{color}装饰点缀，留白充足。

【核心内容】
- 页面标题：「{content.p5_title}」
  - 位置：画面顶部，距顶8%
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度50%

- 清单内容（两列排列）：
  左列：
  - checkbox「{content.checklist[0]}」
  - checkbox「{content.checklist[1]}」
  - checkbox「{content.checklist[2]}」
  - checkbox「{content.checklist[3]}」
  右列：
  - checkbox「{content.checklist[4]}」
  - checkbox「{content.checklist[5]}」
  - checkbox「{content.checklist[6]}」
  - checkbox「{content.checklist[7]}」
  - 位置：画面中部，两列均匀分布
  - 样式：{color}checkbox图标 + 中灰色(#555555)文字

【装饰元素】
- checkbox使用{color}
- 列之间有分隔线

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文""",

        "P6": f"""【图片类型】小红书轮播图内页-示例展示
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务信息图卡片。雾白背景(#FAFAFA)，{color}装饰点缀，留白充足。

【核心内容】
- 页面标题：「{content.p6_title}」
  - 位置：画面顶部，距顶8%
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度50%

- 截图展示框（3个手机边框）：
  - 截图1标注：「{content.screenshot_labels[0]}」
  - 截图2标注：「{content.screenshot_labels[1]}」
  - 截图3标注：「{content.screenshot_labels[2]}」
  - 位置：画面中部，手机边框带阴影
  - 边框样式：深灰色(#333333)圆角边框
  - 标注样式：{color}标签 + 白色文字
  - 截图内部留浅灰色占位

【装饰元素】
- 手机边框带轻微阴影
- {color}标注箭头

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文""",

        "P7": f"""【图片类型】小红书轮播图内页-执行步骤
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务信息图卡片。雾白背景(#FAFAFA)，{color}装饰点缀，留白充足。

【核心内容】
- 页面标题：「{content.p7_title}」
  - 位置：画面顶部，距顶8%
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度50%

- 三步卡片（纵向排列）：
  - Step1：「{content.actions[0][0]}」
    - 说明：「{content.actions[0][1]}」
  - Step2：「{content.actions[1][0]}」
    - 说明：「{content.actions[1][1]}」
  - Step3：「{content.actions[2][0]}」
    - 说明：「{content.actions[2][1]}」
  - 位置：画面中部，3个卡片纵向排列
  - 卡片样式：白色卡片(#FFFFFF)，{color}左边框
  - 序号：{color}大号数字
  - 行动文字：深灰色(#333333)粗体
  - 说明文字：中灰色(#555555)细体

【装饰元素】
- 卡片左侧{color}竖线装饰
- 卡片间有适当间距

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文""",

        "P8": f"""【图片类型】小红书轮播图CTA页
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简商务CTA卡片。{color}渐变背景或雾白背景，大面积留白，行动导向。

【核心内容】
- CTA主文案：「{content.cta_action}」
  - 位置：画面中央偏上
  - 字体：现代无衬线粗体，{color}
  - 大小：占画面宽度70%
  - 效果：醒目突出

- 我发你：
  - 获取项1：「{content.cta_items[0]}」
  - 获取项2：「{content.cta_items[1]}」
  - 获取项3：「{content.cta_items[2]}」
  - 位置：CTA主文案下方
  - 样式：白色卡片 + {color}勾选图标
  - 文字：深灰色(#333333)

【装饰元素】
- 底部装饰线
- 轻微紧迫感视觉

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文、微信号、二维码""",
    }

    return prompts


# ========== 预设内容模板 ==========

def get_preset_content(topic: str = "AI选题工具") -> VisualPackContent:
    """
    获取预设内容模板

    Args:
        topic: 内容主题

    Returns:
        填充好的内容数据
    """
    presets = {
        "AI选题工具": VisualPackContent(
            main_title="AI帮你日更不用愁",
            sub_title="1天8条高质量脚本",
            p2_title="是不是每天都在经历这些？",
            pain_points=["选题想破头也没灵感", "写完一看全是废话", "发了半天没人看"],
            p3_title="为什么内容越做越累？",
            reasons=["没有选题系统", "没有内容模板", "没有质检标准"],
            p4_title="内容交付5步流程",
            steps_5=["定位诊断", "选题库搭建", "内容日历排期", "脚本批量生成", "质检发布"],
            p5_title="脚本质检清单",
            checklist=["开头3秒有钩子", "痛点场景具体", "解决方案清晰", "有数据或案例",
                      "口播节奏自然", "画面切换流畅", "CTA明确可执行", "无敏感词违规"],
            p6_title="实际效果展示",
            screenshot_labels=["诊断报告", "内容日历", "脚本模板"],
            p7_title="立刻执行这3步",
            actions=[("评论【诊断】", "获取8题自测问卷"),
                    ("完成问卷", "5分钟找到内容卡点"),
                    ("领取30天行动清单", "按清单执行即可")],
            cta_action="评论【诊断】",
            cta_items=["8题五维评分", "30天行动清单", "定制化改造建议"]
        ),
        "内容中台": VisualPackContent(
            main_title="告别内容焦虑",
            sub_title="一套系统解决所有问题",
            p2_title="做内容为什么这么累？",
            pain_points=["每天选题靠灵感", "脚本写得想秃头", "发布全靠手动"],
            p3_title="问题出在哪里？",
            reasons=["没有内容流水线", "全靠人工复制粘贴", "无法批量复制"],
            p4_title="内容中台搭建流程",
            steps_5=["账号定位梳理", "选题库建设", "脚本模板沉淀", "自动化排期", "数据复盘"],
            p5_title="内容SOP清单",
            checklist=["选题来源明确", "脚本模板标准", "发布时间固定", "数据每周复盘",
                      "爆款拆解归档", "素材库分类", "协作流程清晰", "交付标准量化"],
            p6_title="系统效果展示",
            screenshot_labels=["选题库", "脚本生成", "数据看板"],
            p7_title="3步搭建你的内容中台",
            actions=[("私信【SOP】", "获取搭建指南"),
                    ("对照清单搭建", "1周完成基础框架"),
                    ("持续优化迭代", "效率翻倍")],
            cta_action="私信【SOP】",
            cta_items=["内容中台搭建指南", "SOP模板全套", "1对1答疑"]
        ),
    }

    return presets.get(topic, VisualPackContent())


# ========== 主执行函数 ==========

def get_generator():
    """获取合适的生成器"""
    if USE_PLAYWRIGHT and PLAYWRIGHT_AVAILABLE:
        return GeminiImageGenerator()
    elif API_AVAILABLE:
        return GeminiAPIGenerator(model="pro")  # 使用Gemini 3.0 Pro模型
    else:
        raise RuntimeError("没有可用的生成器，请安装playwright或gemini-webapi")


async def generate_visual_pack(
    account: str = "A",
    topic: str = "AI选题工具",
    content: VisualPackContent = None,
    pages: list = None
) -> dict:
    """
    生成指定账号的完整视觉包

    Args:
        account: 账号类型 (A/B/C)
        topic: 笔记主题（用于获取预设内容）
        content: 自定义内容，优先于topic
        pages: 要生成的页面列表，默认全部8张

    Returns:
        生成结果字典
    """
    generator = get_generator()

    try:
        await generator.start()

        # 获取内容
        if content is None:
            content = get_preset_content(topic)

        # 获取提示词
        prompts = get_visual_prompts(account, content)

        # 如果指定了页面列表，只生成指定的
        if pages:
            prompts = {k: v for k, v in prompts.items() if k in pages}

        results = await generator.generate_visual_pack(prompts, account)

        # 打印汇总
        print("\n" + "=" * 50)
        print("生成汇总")
        print("=" * 50)

        success_count = 0
        for page_type, path in results.items():
            status = "[OK]" if path else "[FAIL]"
            if path:
                success_count += 1
            print(f"{status} {page_type}: {path or '生成失败'}")

        print(f"\n成功: {success_count}/{len(results)}")

        return results

    finally:
        await generator.close()


async def generate_single(
    prompt: str,
    account: str = "A",
    page_type: str = "测试"
) -> str:
    """
    生成单张图片（用于测试）

    Args:
        prompt: 图片提示词
        account: 账号类型
        page_type: 页面类型

    Returns:
        图片保存路径
    """
    generator = get_generator()

    try:
        await generator.start()
        result = await generator.generate_image(prompt, account, page_type)
        return result
    finally:
        await generator.close()


# ========== CLI入口 ==========

def main():
    import argparse

    # 检查提示
    print("\n" + "=" * 60)
    print("小红书视觉包生成器 - Nano Banana Pro")
    print("=" * 60)

    if USE_PLAYWRIGHT and PLAYWRIGHT_AVAILABLE:
        print("\n[模式] Playwright连接Chrome（支持手动选择模型）")
        print("\n[使用步骤]")
        print("1. 启动Chrome: start chrome --remote-debugging-port=9222")
        print("2. 打开 gemini.google.com")
        print("3. 选择 Nano Banana Pro 模型")
        print("4. 运行此脚本")
        print("-" * 60)
    elif API_AVAILABLE:
        print("\n[模式] Gemini API（使用cookies）")
        if not os.getenv("GEMINI_SECURE_1PSID"):
            print("\n[配置] 需要设置cookies环境变量")
            print("-" * 60)
    else:
        print("\n[错误] 没有可用的生成器")
        print("请安装: pip install playwright gemini-webapi")
        return

    parser = argparse.ArgumentParser(description="小红书视觉包生成器")
    parser.add_argument(
        "--account", "-a",
        type=str,
        default="A",
        choices=["A", "B", "C"],
        help="账号类型：A(转化号)/B(交付号)/C(观点号)"
    )
    parser.add_argument(
        "--topic", "-t",
        type=str,
        default="AI选题工具",
        help="笔记主题（预设：AI选题工具、内容中台）"
    )
    parser.add_argument(
        "--pages", "-p",
        type=str,
        nargs="+",
        help="指定生成的页面，如: --pages 封面 P2 P3"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="测试模式：只生成一张封面图"
    )

    args = parser.parse_args()

    if args.test:
        # 测试模式
        print("=" * 50)
        print("测试模式：生成一张封面图（带中文文案）")
        print("=" * 50)

        content = get_preset_content(args.topic)
        prompts = get_visual_prompts(args.account, content)
        test_prompt = prompts["封面"]

        print(f"\n[预览提示词]:\n{test_prompt[:500]}...\n")

        result = asyncio.run(generate_single(test_prompt, args.account, "封面-测试"))
        print(f"\n结果: {result}")

    else:
        # 完整生成模式
        print("=" * 50)
        print(f"生成 {args.account}号 视觉包")
        print(f"主题: {args.topic}")
        if args.pages:
            print(f"指定页面: {', '.join(args.pages)}")
        print("=" * 50)

        asyncio.run(generate_visual_pack(
            account=args.account,
            topic=args.topic,
            pages=args.pages
        ))


if __name__ == "__main__":
    main()
