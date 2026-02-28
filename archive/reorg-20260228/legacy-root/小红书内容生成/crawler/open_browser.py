"""
简单版：打开浏览器保持10分钟
"""

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright


async def main():
    print("启动浏览器...")

    playwright = await async_playwright().start()

    # 使用独立的用户目录
    profile_dir = Path(__file__).parent / "browser_profiles" / "account_A"
    profile_dir.mkdir(parents=True, exist_ok=True)

    browser = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"]
    )

    page = browser.pages[0] if browser.pages else await browser.new_page()

    print("打开创作中心...")
    await page.goto("https://creator.xiaohongshu.com/publish/publish")

    print("\n" + "=" * 50)
    print("浏览器已打开！")
    print("请手动操作发布")
    print("10分钟后自动关闭")
    print("=" * 50)

    # 保持10分钟
    await asyncio.sleep(600)

    await browser.close()
    await playwright.stop()
    print("完成")


if __name__ == "__main__":
    asyncio.run(main())
