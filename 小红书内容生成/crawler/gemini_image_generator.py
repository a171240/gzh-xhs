"""
Gemini图片生成器 - 使用Playwright自动化
支持两种方式连接Chrome：
1. CDP连接：连接到已运行的Chrome浏览器（推荐，保留登录状态）
2. 独立启动：启动新的浏览器实例

功能：
1. 自动发送图片生成提示词到Gemini
2. 等待图片生成完成
3. 下载生成的图片到本地

使用方式1（CDP连接 - 推荐）：
1. 先用以下命令启动Chrome（启用远程调试）：
   Windows: start chrome --remote-debugging-port=9222
   Mac: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222
2. 在Chrome中登录Gemini
3. 运行脚本

使用方式2（独立浏览器）：
1. 关闭所有Chrome窗口
2. 运行脚本
"""

import asyncio
import base64
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

try:
    from playwright.async_api import async_playwright, Page, BrowserContext, Browser
except ImportError:
    print("请先安装Playwright: pip install playwright && playwright install chromium")
    raise


class GeminiImageGenerator:
    """Gemini图片生成自动化类"""

    def __init__(self, output_dir: str = None):
        """
        初始化Gemini图片生成器

        Args:
            output_dir: 图片输出目录，默认为 ./generated_images
        """
        if output_dir is None:
            # 默认输出到项目根目录下的generated_images
            base_dir = Path(__file__).parent.parent
            output_dir = base_dir / "generated_images"

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.connection_mode = None  # "cdp" or "standalone"

    async def start(self, mode: str = "auto", debug_port: int = 9222) -> None:
        """
        启动或连接浏览器

        Args:
            mode: 连接模式
                - "auto": 自动选择（先尝试CDP，失败则用独立浏览器）
                - "cdp": 连接到已运行的Chrome（推荐）
                - "standalone": 启动独立浏览器
            debug_port: Chrome远程调试端口（仅cdp模式使用）
        """
        self.playwright = await async_playwright().start()
        self.connection_mode = mode

        if mode == "cdp":
            await self._connect_cdp(debug_port)
        elif mode == "standalone":
            await self._launch_standalone()
        else:  # auto mode
            try:
                await self._connect_cdp(debug_port)
            except Exception as e:
                print(f"\n  CDP连接失败，切换到独立浏览器模式...")
                await self._launch_standalone()

    async def _connect_cdp(self, port: int = 9222) -> None:
        """通过CDP连接到已运行的Chrome"""
        print(f"正在连接到Chrome (端口 {port})...")

        try:
            # 连接到已运行的Chrome
            self.browser = await self.playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            print("  [OK] CDP连接成功")

            # 获取现有的context和page
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
                pages = self.context.pages
                if pages:
                    # 查找Gemini页面
                    for page in pages:
                        if "gemini.google.com" in page.url:
                            self.page = page
                            print(f"  [OK] 找到Gemini页面")
                            return

                    # 没有找到Gemini页面，使用第一个页面
                    self.page = pages[0]
                    print(f"  当前页面: {self.page.url}")
                else:
                    self.page = await self.context.new_page()
            else:
                self.context = await self.browser.new_context()
                self.page = await self.context.new_page()

            # 导航到Gemini
            if "gemini.google.com" not in self.page.url:
                print("  正在打开Gemini...")
                await self.page.goto("https://gemini.google.com/app", wait_until="networkidle")
                await asyncio.sleep(2)

        except Exception as e:
            print(f"  [FAIL] CDP连接失败: {e}")
            print("\n请按以下步骤操作：")
            print("1. 关闭所有Chrome窗口")
            print("2. 运行以下命令启动Chrome（启用远程调试）：")
            print('   start chrome --remote-debugging-port=9222')
            print("3. 在Chrome中登录 https://gemini.google.com")
            print("4. 重新运行此脚本")
            raise

    async def _launch_standalone(self) -> None:
        """启动独立的浏览器实例（使用Playwright内置Chromium）"""
        print("正在启动独立浏览器...")

        # 使用独立的用户目录（避免与Chrome冲突）
        temp_user_dir = Path(__file__).parent / "chromium_profile"
        temp_user_dir.mkdir(exist_ok=True)

        try:
            # 使用Playwright内置的Chromium，不需要用户Chrome
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(temp_user_dir),
                headless=False,
                viewport={"width": 1280, "height": 900},
                # 不使用channel参数，使用Playwright自带的Chromium
            )
            print("  [OK] 浏览器启动成功")

            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = await self.context.new_page()

            # 导航到Gemini
            print("  正在打开Gemini...")
            await self.page.goto("https://gemini.google.com/app", wait_until="networkidle")
            await asyncio.sleep(2)

            # 检查登录状态
            try:
                await self.page.wait_for_selector(
                    'div[contenteditable="true"], textarea, [role="textbox"]',
                    timeout=10000
                )
                print("  [OK] Gemini已就绪")
            except:
                print("  [WARN] 需要登录Gemini")
                print("  请在弹出的浏览器中登录Google账号...")
                print("  等待登录完成（最长120秒）...")

                # 等待登录完成（检测输入框出现）
                try:
                    await self.page.wait_for_selector(
                        'div[contenteditable="true"], textarea, [role="textbox"]',
                        timeout=120000  # 2分钟等待登录
                    )
                    print("  [OK] 登录成功，Gemini已就绪")
                except:
                    print("  [FAIL] 登录超时，请手动登录后重试")
                    raise Exception("Gemini登录超时")

        except Exception as e:
            print(f"  [FAIL] 启动失败: {e}")
            raise


    async def generate_image(
        self,
        prompt: str,
        account: str = "A",
        page_type: str = "封面"
    ) -> Optional[str]:
        """
        发送提示词到Gemini并下载生成的图片

        Args:
            prompt: 图片生成提示词
            account: 账号标识 (A/B/C)
            page_type: 页面类型 (封面/P2/P3等)

        Returns:
            保存的图片路径，失败返回None
        """
        if not self.page:
            raise RuntimeError("浏览器未启动，请先调用start()")

        print(f"正在生成图片: {account}号-{page_type}")

        try:
            # 1. 定位输入框
            input_selectors = [
                'div[contenteditable="true"]',
                'textarea',
                '[role="textbox"]',
                '.ql-editor',
            ]

            input_element = None
            for selector in input_selectors:
                try:
                    input_element = await self.page.wait_for_selector(
                        selector,
                        timeout=5000,
                        state="visible"
                    )
                    if input_element:
                        break
                except:
                    continue

            if not input_element:
                print("  错误：找不到输入框")
                return None

            # 2. 清空并输入提示词
            await input_element.click()
            await asyncio.sleep(0.5)

            # 添加图片生成前缀（告诉Gemini生成图片）
            full_prompt = f"请生成一张图片：{prompt}"

            # 使用键盘输入（更可靠）
            await self.page.keyboard.type(full_prompt, delay=10)
            await asyncio.sleep(0.5)

            # 3. 点击发送按钮
            send_selectors = [
                'button[aria-label="发送消息"]',
                'button[aria-label="Send message"]',
                'button[data-test-id="send-button"]',
                'button:has-text("发送")',
                '[aria-label*="send" i]',
            ]

            sent = False
            for selector in send_selectors:
                try:
                    button = await self.page.query_selector(selector)
                    if button and await button.is_visible():
                        await button.click()
                        sent = True
                        break
                except:
                    continue

            if not sent:
                # 尝试按回车发送
                await self.page.keyboard.press("Enter")

            print("  提示词已发送，等待生成...")

            # 4. 等待图片生成（最长等待90秒）
            image_element = await self._wait_for_image(timeout=90000)

            if not image_element:
                print("  警告：未检测到生成的图片")
                return None

            # 5. 下载图片
            image_path = await self._download_image(image_element, account, page_type)

            if image_path:
                print(f"  图片已保存: {image_path}")

            return image_path

        except Exception as e:
            print(f"  生成失败: {e}")
            return None

    async def _wait_for_image(self, timeout: int = 60000) -> Optional[any]:
        """等待图片生成完成"""
        image_selectors = [
            'img[data-test-id="generated-image"]',
            'img[alt*="Generated"]',
            'img[alt*="生成"]',
            'img[src*="googleusercontent"]',
            'div[role="img"] img',
            '.response-content img',
        ]

        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout:
            for selector in image_selectors:
                try:
                    # 查找新生成的图片
                    images = await self.page.query_selector_all(selector)
                    if images:
                        # 返回最后一个图片（最新生成的）
                        return images[-1]
                except:
                    continue

            # 检查是否还在加载
            try:
                loading = await self.page.query_selector('[aria-busy="true"]')
                if loading:
                    await asyncio.sleep(2)
                    continue
            except:
                pass

            await asyncio.sleep(2)

        return None

    async def _download_image(
        self,
        image_element,
        account: str,
        page_type: str
    ) -> Optional[str]:
        """下载图片到本地"""
        try:
            # 创建日期目录
            date_dir = self.output_dir / datetime.now().strftime("%Y-%m-%d")
            date_dir.mkdir(exist_ok=True)

            # 生成文件名
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{account}-{page_type}-{timestamp}.png"
            filepath = date_dir / filename

            # 获取图片src
            src = await image_element.get_attribute("src")

            if not src:
                print("    警告：图片src为空")
                return None

            # 方式1：base64图片直接保存
            if src.startswith("data:image"):
                # 提取base64数据
                try:
                    base64_data = src.split(",")[1]
                    image_data = base64.b64decode(base64_data)
                    with open(filepath, "wb") as f:
                        f.write(image_data)
                    return str(filepath)
                except Exception as e:
                    print(f"    base64解码失败: {e}")

            # 方式2：URL图片，使用截图方式保存
            try:
                await image_element.screenshot(path=str(filepath))
                return str(filepath)
            except Exception as e:
                print(f"    截图保存失败: {e}")

            # 方式3：尝试下载URL
            if src.startswith("http"):
                try:
                    response = await self.page.request.get(src)
                    if response.ok:
                        with open(filepath, "wb") as f:
                            f.write(await response.body())
                        return str(filepath)
                except Exception as e:
                    print(f"    URL下载失败: {e}")

            return None

        except Exception as e:
            print(f"    下载失败: {e}")
            return None

    async def generate_visual_pack(
        self,
        prompts: Dict[str, str],
        account: str = "A"
    ) -> Dict[str, Optional[str]]:
        """
        批量生成一篇笔记的完整视觉包（8张图）

        Args:
            prompts: 8张图的提示词字典
                {
                    "封面": "竖版3:4，极简奢华杂志封面...",
                    "P2": "竖版3:4，共鸣场景卡片...",
                    "P3": "...",
                    ...
                }
            account: 账号标识 (A/B/C)

        Returns:
            生成的图片路径字典 {"封面": "/path/to/image.png", ...}
        """
        results = {}

        print(f"\n{'='*50}")
        print(f"开始生成 {account}号 视觉包（共{len(prompts)}张图）")
        print(f"{'='*50}\n")

        for i, (page_type, prompt) in enumerate(prompts.items(), 1):
            print(f"\n[{i}/{len(prompts)}] 正在生成 {page_type}...")

            try:
                image_path = await self.generate_image(prompt, account, page_type)
                results[page_type] = image_path

                if image_path:
                    print(f"  [OK] 完成")
                else:
                    print(f"  [WARN] 未能保存图片")

                # 生成间隔，避免请求过快
                if i < len(prompts):
                    print("  等待3秒后继续...")
                    await asyncio.sleep(3)

                    # 开始新对话（避免上下文混乱）
                    await self._new_chat()

            except Exception as e:
                print(f"  [FAIL] 失败: {e}")
                results[page_type] = None

        return results

    async def _new_chat(self) -> None:
        """开始新对话"""
        new_chat_selectors = [
            'button[aria-label="新对话"]',
            'button[aria-label="New chat"]',
            'button:has-text("新对话")',
            '[data-test-id="new-chat"]',
        ]

        for selector in new_chat_selectors:
            try:
                button = await self.page.query_selector(selector)
                if button and await button.is_visible():
                    await button.click()
                    await self.page.wait_for_load_state("networkidle")
                    await asyncio.sleep(1)
                    return
            except:
                continue

        # 备选：刷新页面
        try:
            await self.page.reload()
            await self.page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
        except:
            pass

    async def close(self) -> None:
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("\n浏览器已关闭")


# 便捷函数
async def generate_single_image(prompt: str, account: str = "A", page_type: str = "封面") -> Optional[str]:
    """
    便捷函数：生成单张图片

    Args:
        prompt: 图片提示词
        account: 账号标识
        page_type: 页面类型

    Returns:
        图片保存路径
    """
    generator = GeminiImageGenerator()
    try:
        await generator.start()
        result = await generator.generate_image(prompt, account, page_type)
        return result
    finally:
        await generator.close()


# 测试入口
if __name__ == "__main__":
    async def test():
        generator = GeminiImageGenerator()
        try:
            await generator.start()

            # 测试生成一张封面图
            test_prompt = """竖版3:4，极简奢华杂志封面设计，
背景珍珠白到浅灰渐变，大面积留白，
点缀少量紫色发光线条/粒子，
中心标题区域留空用于后期加字，
整体高级、克制、干净，商业SaaS气质，超清"""

            result = await generator.generate_image(test_prompt, "A", "封面-测试")

            print(f"\n测试结果: {result}")

        finally:
            await generator.close()

    asyncio.run(test())
