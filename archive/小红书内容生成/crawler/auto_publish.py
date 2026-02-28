"""
小红书全自动发布脚本
无需任何手动确认，一键完成发布
"""

import asyncio
import json
from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright


async def auto_publish(content_file: str = "publish_content_A.json"):
    """全自动发布流程"""

    print("=" * 60)
    print("小红书全自动发布")
    print("=" * 60)

    # 1. 读取发布内容
    content_path = Path(__file__).parent / content_file
    with open(content_path, 'r', encoding='utf-8') as f:
        content = json.load(f)

    print(f"\n标题: {content['title']}")
    print(f"图片: {len(content['images'])}张")

    # 2. 启动浏览器
    print("\n[1/7] 启动浏览器...")
    playwright = await async_playwright().start()

    profile_dir = Path(__file__).parent / "browser_profiles" / "account_A"
    profile_dir.mkdir(parents=True, exist_ok=True)

    browser = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"]
    )

    page = browser.pages[0] if browser.pages else await browser.new_page()

    try:
        # 3. 打开创作中心（直接用URL参数打开图文模式）
        print("[2/6] 打开图文发布页面...")
        await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official&target=image", timeout=60000)
        await page.wait_for_timeout(3000)

        # 4. 上传图片
        print("[3/6] 上传图片...")
        abs_paths = [str(Path(p).resolve()) for p in content['images']]

        for i, img_path in enumerate(abs_paths, 1):
            print(f"  上传 {i}/{len(abs_paths)}: {Path(img_path).name}")

            # 获取文件上传控件
            upload_input = await page.query_selector('input[type="file"]')
            if upload_input:
                await upload_input.set_input_files(img_path)
                await page.wait_for_timeout(2000)
            else:
                print(f"  [FAIL] 未找到上传控件")
                break

        print("  图片上传完成")
        await page.wait_for_timeout(3000)

        # 5. 填写标题
        print("[4/6] 填写标题...")
        title_selectors = [
            'input[placeholder*="填写标题"]',
            'input.d-text[placeholder*="标题"]',
            'input[placeholder*="标题"]',
        ]
        title_filled = False
        for sel in title_selectors:
            try:
                title_input = await page.query_selector(sel)
                if title_input:
                    await title_input.click()
                    await title_input.fill(content['title'][:20])
                    title_filled = True
                    print(f"  标题填写成功")
                    break
            except Exception as e:
                continue

        if not title_filled:
            print("  [WARN] 未找到标题输入框")

        await page.wait_for_timeout(1000)

        # 6. 填写正文
        print("[5/6] 填写正文...")
        content_selectors = [
            'div.tiptap.ProseMirror',
            'div.ProseMirror[contenteditable="true"]',
            '[contenteditable="true"]',
        ]
        content_filled = False
        for sel in content_selectors:
            try:
                content_area = await page.query_selector(sel)
                if content_area:
                    await content_area.click()
                    # 使用键盘输入
                    await page.keyboard.type(content['content'], delay=10)
                    content_filled = True
                    print(f"  正文填写成功")
                    break
            except Exception as e:
                continue

        if not content_filled:
            print("  [WARN] 未找到正文输入框")

        await page.wait_for_timeout(2000)

        # 7. 点击发布
        print("[6/6] 点击发布...")
        publish_selectors = [
            'button:has-text("发布")',
            'button.d-button:has-text("发布")',
        ]
        published = False
        for sel in publish_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    # 检查按钮是否可点击
                    is_disabled = await btn.get_attribute('disabled')
                    if is_disabled:
                        print(f"  发布按钮暂时不可用，等待...")
                        await page.wait_for_timeout(3000)

                    await btn.click()
                    published = True
                    print(f"  已点击发布按钮")
                    break
            except:
                continue

        if not published:
            # 尝试找所有按钮
            buttons = await page.query_selector_all('button')
            for btn in buttons:
                text = await btn.inner_text()
                if '发布' in text and '暂存' not in text:
                    await btn.click()
                    published = True
                    print(f"  发布按钮点击成功 (文本匹配)")
                    break

        # 等待发布完成
        await page.wait_for_timeout(5000)

        print("\n" + "=" * 60)
        if published:
            print("[OK] 发布流程完成！")
            print("请检查小红书创作中心确认发布状态")
        else:
            print("[WARN] 发布按钮未找到，请手动点击发布")
        print("=" * 60)

        # 保持浏览器打开30秒让用户确认
        print("\n浏览器将在30秒后关闭，请确认发布状态...")
        await page.wait_for_timeout(30000)

    except Exception as e:
        print(f"\n[ERROR] 发布过程出错: {e}")
        print("浏览器保持打开，请手动完成...")
        await page.wait_for_timeout(60000)

    finally:
        await browser.close()
        await playwright.stop()
        print("\n浏览器已关闭")


if __name__ == "__main__":
    asyncio.run(auto_publish())
