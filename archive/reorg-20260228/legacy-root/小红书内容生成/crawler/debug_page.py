"""
调试脚本：打印页面元素信息
"""

import asyncio
from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright


async def debug_page():
    """调试页面元素"""

    print("=" * 60)
    print("调试模式：分析创作中心页面元素")
    print("=" * 60)

    playwright = await async_playwright().start()

    profile_dir = Path(__file__).parent / "browser_profiles" / "account_A"
    browser = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        viewport={"width": 1280, "height": 900},
    )

    page = browser.pages[0] if browser.pages else await browser.new_page()

    # 直接打开图文发布页面（使用URL参数）
    print("\n打开图文发布页面...")
    await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official&target=image", timeout=60000)
    await page.wait_for_timeout(5000)

    # 截图确认页面状态
    await page.screenshot(path="debug_step1.png")
    print("  已保存截图: debug_step1.png")

    # 上传测试图片
    print("\n上传测试图片...")
    try:
        # 使用 Path 对象处理中文路径
        img_dir = Path(__file__).parent.parent / "generated_images" / "2026-01-12"
        img_files = list(img_dir.glob("A-01-*.png"))
        if img_files:
            img_path = str(img_files[0].resolve())
            print(f"  图片路径: {img_path}")

            upload_input = await page.query_selector('input[type="file"]')
            if upload_input:
                await upload_input.set_input_files(img_path)
                print("  图片上传中，等待10秒...")
                await page.wait_for_timeout(10000)
                print("  图片上传完成")
            else:
                print("  未找到上传控件")
        else:
            print("  未找到测试图片")
    except Exception as e:
        print(f"  上传失败: {e}")

    # 检查是否有iframe
    print("\n检查页面结构...")
    frames = page.frames
    print(f"  页面frame数量: {len(frames)}")
    for i, frame in enumerate(frames):
        print(f"  Frame {i}: {frame.url[:80]}")

    # 分析页面元素
    print("\n" + "=" * 60)
    print("页面元素分析")
    print("=" * 60)

    # 0. 打印当前URL
    print(f"\n当前URL: {page.url}")

    # 0.5 打印页面标题
    title = await page.title()
    print(f"页面标题: {title}")

    # 1. 所有输入框
    print("\n[输入框 input]")
    inputs = await page.query_selector_all('input')
    for i, inp in enumerate(inputs):
        try:
            inp_type = await inp.get_attribute('type') or ''
            placeholder = await inp.get_attribute('placeholder') or ''
            class_name = await inp.get_attribute('class') or ''
            name = await inp.get_attribute('name') or ''
            print(f"  {i+1}. type={inp_type}, name={name}, placeholder={placeholder[:30]}, class={class_name[:40]}")
        except:
            pass

    # 2. 文本区域
    print("\n[文本区域 textarea]")
    textareas = await page.query_selector_all('textarea')
    for i, ta in enumerate(textareas):
        try:
            placeholder = await ta.get_attribute('placeholder') or ''
            class_name = await ta.get_attribute('class') or ''
            print(f"  {i+1}. placeholder={placeholder[:30]}, class={class_name[:40]}")
        except:
            pass

    # 3. 可编辑区域
    print("\n[可编辑区域 contenteditable]")
    editables = await page.query_selector_all('[contenteditable="true"]')
    for i, ed in enumerate(editables):
        try:
            class_name = await ed.get_attribute('class') or ''
            tag = await ed.evaluate('el => el.tagName')
            print(f"  {i+1}. tag={tag}, class={class_name[:50]}")
        except:
            pass

    # 4. 所有按钮（包括disabled）
    print("\n[所有按钮 button]")
    buttons = await page.query_selector_all('button')
    for i, btn in enumerate(buttons):
        try:
            text = (await btn.inner_text()).strip().replace('\n', ' ')
            class_name = await btn.get_attribute('class') or ''
            disabled = await btn.get_attribute('disabled')
            print(f"  {i+1}. text={text[:30]}, disabled={disabled}, class={class_name[:40]}")
        except:
            pass

    # 5. 带placeholder的div
    print("\n[带placeholder的元素]")
    placeholders = await page.query_selector_all('[placeholder]')
    for i, ph in enumerate(placeholders):
        try:
            placeholder = await ph.get_attribute('placeholder') or ''
            tag = await ph.evaluate('el => el.tagName')
            class_name = await ph.get_attribute('class') or ''
            print(f"  {i+1}. tag={tag}, placeholder={placeholder[:30]}, class={class_name[:40]}")
        except:
            pass

    # 6. 查找特定关键词的元素
    print("\n[包含'标题'的元素]")
    title_elems = await page.query_selector_all('*:has-text("标题")')
    for i, el in enumerate(title_elems[:10]):
        try:
            tag = await el.evaluate('el => el.tagName')
            class_name = await el.get_attribute('class') or ''
            print(f"  {i+1}. tag={tag}, class={class_name[:50]}")
        except:
            pass

    # 7. 查找发布相关元素
    print("\n[包含'发布'的元素]")
    publish_elems = await page.query_selector_all('*:has-text("发布")')
    for i, el in enumerate(publish_elems[:10]):
        try:
            tag = await el.evaluate('el => el.tagName')
            class_name = await el.get_attribute('class') or ''
            text = (await el.inner_text()).strip()[:30]
            print(f"  {i+1}. tag={tag}, text={text}, class={class_name[:40]}")
        except:
            pass

    print("\n" + "=" * 60)
    print("浏览器保持打开15秒...")
    print("=" * 60)

    await page.wait_for_timeout(15000)

    await browser.close()
    await playwright.stop()


if __name__ == "__main__":
    asyncio.run(debug_page())
