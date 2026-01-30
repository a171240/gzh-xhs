"""
手动发布辅助脚本
打开创作中心页面，显示要发布的内容，由用户手动操作
"""

import asyncio
import json
from pathlib import Path
import sys
import io
import time

# 设置输出编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).parent))

from account_manager import AccountManager


async def main():
    print("=" * 60)
    print("小红书手动发布辅助")
    print("=" * 60)

    # 读取发布内容
    content_file = Path(__file__).parent / "publish_content_A.json"
    with open(content_file, 'r', encoding='utf-8') as f:
        content = json.load(f)

    print("\n[发布内容预览]")
    print("-" * 40)
    print(f"标题: {content['title']}")
    print(f"图片数量: {len(content['images'])}张")
    print("-" * 40)

    print("\n图片路径:")
    for i, img in enumerate(content['images'], 1):
        print(f"  {i}. {img}")

    # 保存内容到文本文件方便复制
    output_file = Path(__file__).parent / "publish_text.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"标题：\n{content['title']}\n\n")
        f.write(f"正文：\n{content['content']}\n\n")
        f.write(f"标签：\n{' '.join(['#' + t for t in content['tags']])}\n")

    print(f"\n内容已保存到: {output_file}")

    print("\n" + "=" * 60)
    print("正在打开小红书创作中心...")
    print("=" * 60)

    manager = AccountManager()

    # 获取页面
    page = await manager.get_page("A")

    # 打开创作中心
    await page.goto("https://creator.xiaohongshu.com/publish/publish", timeout=60000)

    print("\n" + "=" * 60)
    print("创作中心已打开！")
    print("=" * 60)
    print("\n请在浏览器中手动操作：")
    print("1. 上传图片")
    print("2. 填写标题和正文（从 publish_text.txt 复制）")
    print("3. 添加标签")
    print("4. 点击发布")
    print("\n" + "=" * 60)
    print("浏览器将保持打开10分钟...")
    print("发布完成后可以直接关闭浏览器窗口")
    print("=" * 60)

    # 保持浏览器打开10分钟（600秒）
    for i in range(60):
        await asyncio.sleep(10)
        remaining = 600 - (i + 1) * 10
        if remaining > 0 and remaining % 60 == 0:
            print(f"  剩余时间: {remaining // 60} 分钟")

    print("\n超时，正在关闭浏览器...")
    await manager.close_browser()
    print("[OK] 完成！")


if __name__ == "__main__":
    asyncio.run(main())
