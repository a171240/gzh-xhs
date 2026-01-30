"""
小红书发布核心逻辑
使用Playwright自动化发布笔记
"""

import asyncio
import random
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from dataclasses import dataclass, field

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from account_manager import AccountManager, ACCOUNT_CONFIG


# ========== 数据结构 ==========

@dataclass
class NoteData:
    """笔记数据"""
    title: str                          # 标题（≤20字）
    content: str                        # 正文（≤1000字）
    images: List[str]                   # 图片路径列表（1-9张）
    tags: List[str] = field(default_factory=list)  # 标签列表
    topics: List[str] = field(default_factory=list)  # 话题列表
    schedule_time: Optional[str] = None  # 定时发布时间（可选）


@dataclass
class PublishResult:
    """发布结果"""
    success: bool
    note_id: Optional[str] = None
    note_url: Optional[str] = None
    error_message: Optional[str] = None
    publish_time: str = field(default_factory=lambda: datetime.now().isoformat())


# ========== 发布器 ==========

class XiaoHongShuPublisher:
    """小红书笔记发布器"""

    # 创作中心URL - 直接打开图文模式
    CREATION_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&target=image"

    def __init__(self, account_manager: AccountManager = None):
        """
        初始化发布器

        Args:
            account_manager: 账号管理器实例
        """
        self.manager = account_manager or AccountManager()
        self.page: Optional[Page] = None
        self.current_account: Optional[str] = None

    async def _random_delay(self, min_seconds: float = 0.5, max_seconds: float = 2.0):
        """随机延迟，模拟人工操作"""
        delay = random.uniform(min_seconds, max_seconds)
        await asyncio.sleep(delay)

    async def _type_slowly(self, selector: str, text: str, delay_per_char: float = 0.05):
        """慢速输入文字，模拟人工打字"""
        element = await self.page.wait_for_selector(selector, timeout=10000)
        for char in text:
            await element.type(char, delay=int(delay_per_char * 1000))
            # 随机额外延迟
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.1, 0.3))

    async def switch_account(self, account_id: str):
        """切换到指定账号"""
        if self.current_account != account_id:
            print(f"切换到 {account_id}号 ({ACCOUNT_CONFIG[account_id]['name']})")
            self.page = await self.manager.get_page(account_id)
            self.current_account = account_id

    async def check_and_login(self, account_id: str) -> bool:
        """检查登录状态，未登录则执行登录"""
        await self.switch_account(account_id)

        # 先检查本地状态，避免不必要的页面跳转
        state = self.manager.get_state(account_id)
        if state.is_logged_in:
            print(f"  {account_id}号已登录（本地状态）")
            return True

        print(f"{account_id}号未登录，正在启动登录流程...")
        return await self.manager.login(account_id)

    async def _upload_images(self, image_paths: List[str]) -> bool:
        """
        上传图片（逐个上传）

        Args:
            image_paths: 图片路径列表

        Returns:
            是否成功
        """
        print(f"  上传图片 ({len(image_paths)}张)...")

        try:
            # 等待页面完全加载
            await self.page.wait_for_timeout(3000)

            # 转换为绝对路径
            abs_paths = [str(Path(p).resolve()) for p in image_paths]

            # 逐个上传图片
            for i, img_path in enumerate(abs_paths, 1):
                print(f"    上传第 {i}/{len(abs_paths)} 张: {Path(img_path).name}")

                # 每次重新获取上传控件（因为上传后DOM可能变化）
                upload_input = await self.page.wait_for_selector(
                    'input[type="file"]',
                    timeout=10000
                )

                if not upload_input:
                    print(f"    [FAIL] 未找到上传控件")
                    return False

                # 上传单个文件
                await upload_input.set_input_files(img_path)

                # 等待上传完成
                await self.page.wait_for_timeout(3000)

            print(f"    [OK] 全部图片上传完成")
            return True

        except PlaywrightTimeout:
            print(f"    [FAIL] 图片上传超时")
            return False
        except Exception as e:
            print(f"    [FAIL] 图片上传失败: {e}")
            return False

    async def _fill_title(self, title: str) -> bool:
        """填写标题"""
        print(f"  填写标题: {title[:20]}...")

        try:
            # 2026年最新选择器
            title_selectors = [
                'input[placeholder*="填写标题"]',
                'input.d-text[placeholder*="标题"]',
                'input[placeholder*="标题"]',
            ]

            for selector in title_selectors:
                try:
                    title_input = await self.page.wait_for_selector(selector, timeout=5000)
                    if title_input:
                        await title_input.click()
                        await self._random_delay(0.2, 0.5)
                        await title_input.fill("")  # 清空
                        await title_input.fill(title[:20])  # 最多20字
                        print(f"    [OK] 标题填写完成")
                        return True
                except:
                    continue

            print(f"    [FAIL] 未找到标题输入框")
            return False

        except Exception as e:
            print(f"    [FAIL] 标题填写失败: {e}")
            return False

    async def _fill_content(self, content: str) -> bool:
        """填写正文"""
        print(f"  填写正文 ({len(content)}字)...")

        try:
            # 2026年最新选择器 - TipTap编辑器
            content_selectors = [
                'div.tiptap.ProseMirror',
                'div.ProseMirror[contenteditable="true"]',
                '[contenteditable="true"]',
            ]

            for selector in content_selectors:
                try:
                    content_input = await self.page.wait_for_selector(selector, timeout=5000)
                    if content_input:
                        await content_input.click()
                        await self._random_delay(0.2, 0.5)

                        # 清空并填写（TipTap编辑器用键盘操作更可靠）
                        await self.page.keyboard.press("Control+a")
                        await self.page.keyboard.press("Delete")
                        await self._random_delay(0.2, 0.5)

                        # 逐段输入（模拟真人打字）
                        await self.page.keyboard.type(content[:1000], delay=10)

                        print(f"    [OK] 正文填写完成")
                        return True
                except:
                    continue

            print(f"    [FAIL] 未找到正文输入框")
            return False

        except Exception as e:
            print(f"    [FAIL] 正文填写失败: {e}")
            return False

    async def _add_tags(self, tags: List[str]) -> bool:
        """添加标签"""
        if not tags:
            return True

        print(f"  添加标签 ({len(tags)}个)...")

        try:
            # 在正文末尾添加标签（小红书格式）
            content_input = await self.page.query_selector('[contenteditable="true"]')
            if content_input:
                await content_input.click()
                await self.page.keyboard.press("End")

                # 添加标签
                for tag in tags[:10]:  # 最多10个标签
                    tag_text = f" #{tag}" if not tag.startswith("#") else f" {tag}"
                    await self.page.keyboard.type(tag_text)
                    await self._random_delay(0.2, 0.5)

                print(f"    [OK] 标签添加完成")
                return True

            return False

        except Exception as e:
            print(f"    [FAIL] 标签添加失败: {e}")
            return False

    async def _add_topics(self, topics: List[str]) -> bool:
        """添加话题"""
        if not topics:
            return True

        print(f"  添加话题 ({len(topics)}个)...")

        try:
            # 查找话题按钮
            topic_button = await self.page.query_selector(
                'text=添加话题, button:has-text("话题"), [class*="topic"]'
            )

            if topic_button:
                await topic_button.click()
                await self._random_delay(0.5, 1.0)

                # 搜索并选择话题
                for topic in topics[:3]:  # 最多3个话题
                    search_input = await self.page.query_selector(
                        'input[placeholder*="搜索"], input[class*="search"]'
                    )
                    if search_input:
                        await search_input.fill(topic)
                        await self._random_delay(1.0, 2.0)

                        # 点击第一个搜索结果
                        first_result = await self.page.query_selector(
                            '[class*="result"] >> nth=0, [class*="item"] >> nth=0'
                        )
                        if first_result:
                            await first_result.click()
                            await self._random_delay(0.3, 0.8)

                print(f"    [OK] 话题添加完成")
                return True

            print(f"    [WARN] 未找到话题按钮，跳过")
            return True

        except Exception as e:
            print(f"    [WARN] 话题添加失败: {e}")
            return True  # 话题添加失败不影响发布

    async def _click_publish(self) -> bool:
        """点击发布按钮"""
        print(f"  点击发布...")

        try:
            # 2026年最新选择器
            publish_selectors = [
                'button:has-text("发布")',
                'button.d-button:has-text("发布")',
            ]

            for selector in publish_selectors:
                try:
                    publish_btn = await self.page.wait_for_selector(selector, timeout=5000)
                    if publish_btn:
                        # 检查按钮是否可点击
                        is_disabled = await publish_btn.get_attribute('disabled')
                        if is_disabled:
                            print(f"    发布按钮暂时不可用，等待...")
                            await self.page.wait_for_timeout(3000)

                        await self._random_delay(0.5, 1.0)
                        await publish_btn.click()

                        # 等待发布完成
                        await asyncio.sleep(5)

                        print(f"    [OK] 发布请求已发送")
                        return True
                except:
                    continue

            print(f"    [FAIL] 未找到发布按钮")
            return False

        except Exception as e:
            print(f"    [FAIL] 发布失败: {e}")
            return False

    async def _schedule_publish(self, schedule_time: str) -> bool:
        """设置定时发布"""
        print(f"  设置定时发布: {schedule_time}...")

        try:
            # 查找定时发布选项
            schedule_btn = await self.page.query_selector(
                'text=定时发布, [class*="schedule"]'
            )

            if schedule_btn:
                await schedule_btn.click()
                await self._random_delay(0.5, 1.0)

                # 设置时间
                time_input = await self.page.query_selector(
                    'input[type="datetime-local"], input[class*="time"]'
                )
                if time_input:
                    await time_input.fill(schedule_time)
                    print(f"    [OK] 定时发布设置完成")
                    return True

            print(f"    [WARN] 未找到定时发布选项")
            return False

        except Exception as e:
            print(f"    [FAIL] 定时发布设置失败: {e}")
            return False

    async def publish(self, account_id: str, note_data: NoteData) -> PublishResult:
        """
        发布笔记

        Args:
            account_id: 账号标识 (A/B/C)
            note_data: 笔记数据

        Returns:
            发布结果
        """
        config = ACCOUNT_CONFIG[account_id]

        print(f"\n{'='*50}")
        print(f"开始发布: {config['name']} ({account_id}号)")
        print(f"标题: {note_data.title}")
        print(f"{'='*50}")

        # 1. 检查是否可以发布
        can_publish, reason = self.manager.can_publish(account_id)
        if not can_publish:
            return PublishResult(
                success=False,
                error_message=reason
            )

        # 2. 检查登录状态
        if not await self.check_and_login(account_id):
            return PublishResult(
                success=False,
                error_message="登录失败"
            )

        try:
            # 3. 打开创作中心（URL已包含图文模式参数）
            print("  打开创作中心...")
            await self.page.goto(self.CREATION_URL, timeout=30000)
            await self.page.wait_for_timeout(3000)

            # 4. 上传图片
            if not await self._upload_images(note_data.images):
                return PublishResult(
                    success=False,
                    error_message="图片上传失败"
                )

            await self._random_delay(1, 2)

            # 5. 填写标题
            if not await self._fill_title(note_data.title):
                return PublishResult(
                    success=False,
                    error_message="标题填写失败"
                )

            await self._random_delay(0.5, 1)

            # 6. 填写正文
            if not await self._fill_content(note_data.content):
                return PublishResult(
                    success=False,
                    error_message="正文填写失败"
                )

            await self._random_delay(0.5, 1)

            # 7. 添加标签
            await self._add_tags(note_data.tags)
            await self._random_delay(0.5, 1)

            # 8. 添加话题
            await self._add_topics(note_data.topics)
            await self._random_delay(0.5, 1)

            # 9. 定时发布或立即发布
            if note_data.schedule_time:
                await self._schedule_publish(note_data.schedule_time)

            if not await self._click_publish():
                return PublishResult(
                    success=False,
                    error_message="点击发布失败"
                )

            # 10. 记录发布
            self.manager.record_publish(
                account_id=account_id,
                success=True,
                title=note_data.title
            )

            print(f"\n[OK] 发布成功！")

            return PublishResult(
                success=True,
                note_url=self.page.url
            )

        except Exception as e:
            error_msg = str(e)
            print(f"\n[FAIL] 发布失败: {error_msg}")

            self.manager.record_publish(
                account_id=account_id,
                success=False,
                title=note_data.title,
                error_message=error_msg
            )

            return PublishResult(
                success=False,
                error_message=error_msg
            )

    async def batch_publish(self, notes: List[Dict]) -> List[PublishResult]:
        """
        批量发布笔记（自动轮转账号）

        Args:
            notes: 笔记列表，每个笔记包含 account_id 和 note_data

        Returns:
            发布结果列表
        """
        results = []

        print(f"\n{'='*60}")
        print(f"批量发布任务: 共 {len(notes)} 篇笔记")
        print(f"{'='*60}")

        for i, note_info in enumerate(notes, 1):
            account_id = note_info.get("account_id", "A")
            note_data = note_info.get("note_data")

            if isinstance(note_data, dict):
                note_data = NoteData(**note_data)

            print(f"\n[{i}/{len(notes)}] 发布到 {account_id}号...")

            result = await self.publish(account_id, note_data)
            results.append(result)

            if result.success and i < len(notes):
                # 发布间隔
                wait_time = random.randint(60, 120)
                print(f"  等待 {wait_time} 秒后继续...")
                await asyncio.sleep(wait_time)

        # 打印汇总
        success_count = sum(1 for r in results if r.success)
        print(f"\n{'='*60}")
        print(f"批量发布完成: 成功 {success_count}/{len(results)}")
        print(f"{'='*60}")

        return results

    async def close(self):
        """关闭发布器"""
        await self.manager.close_browser()


# ========== 测试入口 ==========

if __name__ == "__main__":
    async def test():
        publisher = XiaoHongShuPublisher()

        # 打印账号状态
        publisher.manager.print_status()

        # 测试笔记数据
        test_note = NoteData(
            title="AI帮你日更不用愁",
            content="是不是每天都在经历这些？选题想破头也没灵感，写完一看全是废话，发了半天没人看...",
            images=[
                "generated_images/2026-01-11/A-封面-测试-234948.png"
            ],
            tags=["AI工具", "内容创作", "小红书运营"]
        )

        # 测试发布（需要先登录）
        # result = await publisher.publish("A", test_note)
        # print(f"发布结果: {result}")

        await publisher.close()

    asyncio.run(test())
