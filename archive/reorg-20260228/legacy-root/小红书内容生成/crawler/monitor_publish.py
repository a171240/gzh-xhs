"""
监控脚本：打开创作中心，观察用户手动操作
"""

import asyncio
import json
from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from account_manager import AccountManager


async def main():
    print("=" * 60)
    print("监控模式：观察手动发布流程")
    print("=" * 60)

    manager = AccountManager()
    page = await manager.get_page("A")

    # 打开创作中心
    print("\n正在打开创作中心...")
    await page.goto("https://creator.xiaohongshu.com/publish/publish", timeout=60000)
    await page.wait_for_timeout(3000)

    print("\n" + "=" * 60)
    print("创作中心已打开！")
    print("请按照正常流程手动发布一次")
    print("=" * 60)

    # 监控页面变化
    print("\n开始监控页面元素...")

    for i in range(120):  # 监控10分钟
        await asyncio.sleep(5)

        # 获取当前页面的一些关键元素
        try:
            # 检查上传区域
            upload_inputs = await page.query_selector_all('input[type="file"]')
            print(f"\n[{i*5}秒] 发现 {len(upload_inputs)} 个文件上传控件")

            # 检查标题输入框
            title_inputs = await page.query_selector_all('input')
            print(f"  发现 {len(title_inputs)} 个输入框")

            # 检查文本区域
            textareas = await page.query_selector_all('textarea, [contenteditable="true"]')
            print(f"  发现 {len(textareas)} 个文本区域")

            # 检查发布按钮
            buttons = await page.query_selector_all('button')
            print(f"  发现 {len(buttons)} 个按钮")

            # 获取当前URL
            current_url = page.url
            print(f"  当前URL: {current_url}")

        except Exception as e:
            print(f"  监控异常: {e}")

    await manager.close_browser()


if __name__ == "__main__":
    asyncio.run(main())
