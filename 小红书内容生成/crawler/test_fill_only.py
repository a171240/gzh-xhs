"""
测试脚本：只填写内容，不点发布
用于验证选择器是否正确
"""

import asyncio
import json
from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright


async def test_fill():
    """测试填写流程（不发布）"""

    print("=" * 60)
    print("测试模式：填写内容但不发布")
    print("=" * 60)

    # 1. 读取发布内容
    content_file = Path(__file__).parent / "publish_content_A.json"
    with open(content_file, 'r', encoding='utf-8') as f:
        content = json.load(f)

    print(f"\n标题: {content['title']}")
    print(f"图片: {len(content['images'])}张")

    # 2. 启动浏览器
    print("\n[1/5] 启动浏览器...")
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
        # 3. 打开图文发布页面
        print("[2/5] 打开图文发布页面...")
        await page.goto("https://creator.xiaohongshu.com/publish/publish?source=official&target=image", timeout=60000)
        await page.wait_for_timeout(3000)

        # 4. 上传图片（只上传1张测试）
        print("[3/5] 上传测试图片...")
        img_path = str(Path(content['images'][0]).resolve())
        print(f"  图片路径: {img_path}")

        upload_input = await page.query_selector('input[type="file"]')
        if upload_input:
            await upload_input.set_input_files(img_path)
            print("  图片上传中...")
            await page.wait_for_timeout(5000)
            print("  [OK] 图片上传完成")
        else:
            print("  [FAIL] 未找到上传控件")

        # 5. 填写标题
        print("[4/5] 填写标题...")
        title_input = await page.query_selector('input[placeholder*="填写标题"]')
        if title_input:
            await title_input.click()
            await title_input.fill(content['title'][:20])
            print(f"  [OK] 标题填写完成: {content['title'][:20]}")
        else:
            print("  [FAIL] 未找到标题输入框")

        await page.wait_for_timeout(1000)

        # 6. 填写正文
        print("[5/5] 填写正文...")
        content_area = await page.query_selector('div.tiptap.ProseMirror')
        if content_area:
            await content_area.click()
            # 只输入前100字测试
            test_content = content['content'][:100] + "..."
            await page.keyboard.type(test_content, delay=10)
            print(f"  [OK] 正文填写完成 (测试100字)")
        else:
            print("  [FAIL] 未找到正文编辑器")

        # 截图保存
        await page.screenshot(path="test_fill_result.png")
        print("\n[OK] 已保存截图: test_fill_result.png")

        print("\n" + "=" * 60)
        print("测试完成！请检查浏览器中的填写结果")
        print("确认无误后可以手动点击发布，或关闭浏览器")
        print("=" * 60)

        # 保持浏览器打开60秒
        print("\n浏览器将在60秒后关闭...")
        await page.wait_for_timeout(60000)

    except Exception as e:
        print(f"\n[ERROR] 测试出错: {e}")
        await page.wait_for_timeout(30000)

    finally:
        await browser.close()
        await playwright.stop()
        print("\n浏览器已关闭")


if __name__ == "__main__":
    asyncio.run(test_fill())
