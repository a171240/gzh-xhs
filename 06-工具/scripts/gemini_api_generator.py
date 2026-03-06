"""
Gemini图片生成器 - 使用gemini-webapi库（逆向工程API）

优势：
1. 直接复用你的Gemini会员登录状态
2. 不需要浏览器，后台运行
3. 支持图片生成和保存

使用方式：
1. 从浏览器获取cookies（见下方说明）
2. 设置环境变量或直接传入cookies
3. 调用generate_image生成图片

获取Cookies：
1. 打开Chrome，登录 https://gemini.google.com
2. 按F12打开开发者工具 -> Application -> Cookies
3. 复制 __Secure-1PSID 和 __Secure-1PSIDTS 的值
"""

import asyncio
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

# 自动加载.env文件
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv未安装，跳过

try:
    from gemini_webapi import GeminiClient
except ImportError:
    print("请先安装gemini-webapi: pip install gemini-webapi")
    raise


class GeminiAPIGenerator:
    """使用gemini-webapi的图片生成器"""

    # 可用模型列表（基于gemini-webapi支持的模型）
    MODELS = {
        "pro": "gemini-3.0-pro",                    # Recommended default
        "flash": "gemini-3.0-flash",               # Faster generation
        "3.0-pro": "gemini-3.0-pro",
        "3.0-flash": "gemini-3.0-flash",
        "3.0-flash-thinking": "gemini-3.0-flash-thinking",
        "2.5-pro": "gemini-3.0-pro",               # Backward-compatible alias
        "2.5-flash": "gemini-3.0-flash",           # Backward-compatible alias
    }

    IMAGE_UNAVAILABLE_MARKERS = (
        "无法为您创建任何图片",
        "无法创建任何图片",
        "图片创建功能",
        "can't create any images",
        "unable to create any images",
        "create any images",
        "image creation",
        "not available in your region",
    )

    def __init__(
        self,
        secure_1psid: str = None,
        secure_1psidts: str = None,
        output_dir: str = None,
        model: str = "pro"  # 默认使用Pro模型
    ):
        """
        初始化Gemini API生成器

        Args:
            secure_1psid: __Secure-1PSID cookie值（必需）
            secure_1psidts: __Secure-1PSIDTS cookie值（可选）
            output_dir: 图片输出目录
            model: 模型选择 - "pro"(推荐), "flash"(快速), "1.5-pro"
        """
        # 从环境变量或参数获取cookies
        self.secure_1psid = secure_1psid or os.getenv("GEMINI_SECURE_1PSID", "")
        self.secure_1psidts = secure_1psidts or os.getenv("GEMINI_SECURE_1PSIDTS", "")
        self.proxy = (os.getenv("GEMINI_PROXY", "").strip() or None)

        # 设置模型
        self.model_id = self.MODELS.get(model, self.MODELS["pro"])
        self.model_name = model

        if not self.secure_1psid:
            raise ValueError(
                "需要提供 __Secure-1PSID cookie!\n"
                "获取方式：\n"
                "1. 打开Chrome，登录 https://gemini.google.com\n"
                "2. F12 -> Application -> Cookies -> gemini.google.com\n"
                "3. 复制 __Secure-1PSID 的值"
            )

        # 设置输出目录
        if output_dir is None:
            base_dir = Path(__file__).parent.parent
            output_dir = base_dir / "generated_images"

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.debug_log_path = self.output_dir / "gemini_webapi.log"
        self._loguru_sink_id = None
        if os.getenv("GEMINI_DEBUG_LOG", "1") == "1":
            try:
                from loguru import logger
                self._loguru_sink_id = logger.add(
                    str(self.debug_log_path),
                    level="DEBUG",
                    enqueue=True
                )
            except Exception:
                self._loguru_sink_id = None

        self.client: Optional[GeminiClient] = None
        self.last_error: Optional[str] = None
        self.last_error_code: Optional[str] = None

    @classmethod
    def _classify_no_image_reason(cls, text: str) -> str:
        message = str(text or "").strip()
        if not message:
            return "no_images"
        lowered = message.lower()
        for marker in cls.IMAGE_UNAVAILABLE_MARKERS:
            if marker.lower() in lowered:
                return "image_generation_unavailable"
        return "no_images"

    async def start(self) -> None:
        """初始化Gemini客户端"""
        print(f"正在初始化Gemini API客户端 (模型: {self.model_id})...")
        if self.proxy:
            print(f"  Proxy: {self.proxy}")
        if self._loguru_sink_id is not None:
            print(f"  Debug log: {self.debug_log_path}")

        try:
            self.client = GeminiClient(
                self.secure_1psid,
                self.secure_1psidts,
                proxy=self.proxy
            )
            await self.client.init(
                timeout=60,
                auto_close=False,
                close_delay=300,
                auto_refresh=True
            )
            print("  [OK] Gemini API客户端初始化成功")
        except Exception as e:
            print(f"  [FAIL] 初始化失败: {e}")
            print("\n可能的原因：")
            print("1. Cookie已过期，请重新获取")
            print("2. 网络问题，请检查代理设置")
            raise

    async def generate_image(
        self,
        prompt: str,
        account: str = "A",
        page_type: str = "封面",
        size: str | None = None,
    ) -> Optional[str]:
        """
        生成单张图片

        Args:
            prompt: 图片生成提示词
            account: 账号标识 (A/B/C)
            page_type: 页面类型 (封面/P2/P3等)
            size: 预留的宽高比例参数，供与其他生成器接口对齐

        Returns:
            保存的图片路径，失败返回None
        """
        if not self.client:
            raise RuntimeError("客户端未初始化，请先调用start()")

        self.last_error = None
        self.last_error_code = None
        print(f"正在生成图片: {account}号-{page_type} (模型: {self.model_id})")

        # 构造生成图片的提示词
        full_prompt = f"请生成一张图片：\n\n{prompt}"

        response = None
        try:
            # 调用API生成，指定模型
            response = await self.client.generate_content(full_prompt, model=self.model_id)

            # 打印响应信息用于调试
            print(f"  响应文本: {response.text[:100] if response.text else '无'}...")

            # 检查是否有生成的图片
            if not response.images:
                response_text = str(response.text or "").strip()
                self.last_error = response_text or "Gemini returned no images."
                self.last_error_code = self._classify_no_image_reason(response_text)
                if self.last_error_code == "image_generation_unavailable":
                    print("  [WARN] 当前 Gemini 会话不可用图片创建能力")
                    return None
                print("  [WARN] 未生成图片，尝试更直接的提示...")
                # 重试，更直接地要求生成图片
                retry_prompt = f"Generate an image: {prompt[:500]}"
                response = await self.client.generate_content(retry_prompt, model=self.model_id)
                print(f"  重试响应: {response.text[:100] if response.text else '无'}...")

            if not response.images:
                response_text = str(response.text or "").strip()
                self.last_error = response_text or "Gemini returned no images after retry."
                self.last_error_code = self._classify_no_image_reason(response_text)
                print("  [WARN] 仍未生成图片")
                return None

            # 保存第一张生成的图片
            image = response.images[0]

            # 创建日期目录
            date_dir = self.output_dir / datetime.now().strftime("%Y-%m-%d")
            date_dir.mkdir(exist_ok=True)

            # 生成文件名
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{account}-{page_type}-{timestamp}.png"
            filepath = date_dir / filename

            # 保存图片
            await image.save(path=str(date_dir), filename=filename, verbose=False)

            self.last_error = None
            self.last_error_code = None
            print(f"  [OK] 图片已保存: {filepath}")
            return str(filepath)

        except Exception as e:
            self.last_error = str(e)
            self.last_error_code = "exception"
            print(f"  [FAIL] 生成失败: {e}")
            try:
                from loguru import logger
                logger.exception("generate_image failed")
                if response is not None:
                    logger.debug("response.text=%s", response.text if response.text else "")
                    logger.debug("response.images=%s", len(response.images) if response.images else 0)
            except Exception:
                pass
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
            account: 账号标识 (A/B/C)

        Returns:
            生成的图片路径字典
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
                    print("  等待2秒后继续...")
                    await asyncio.sleep(2)

            except Exception as e:
                print(f"  [FAIL] 失败: {e}")
                results[page_type] = None

        return results

    async def close(self) -> None:
        """关闭客户端"""
        if self.client:
            await self.client.close()
        try:
            from loguru import logger
            if self._loguru_sink_id is not None:
                logger.remove(self._loguru_sink_id)
        except Exception:
            pass
        print("\nGemini API客户端已关闭")


# ========== 便捷函数 ==========

async def generate_single_image(
    prompt: str,
    account: str = "A",
    page_type: str = "封面",
    secure_1psid: str = None,
    secure_1psidts: str = None
) -> Optional[str]:
    """
    便捷函数：生成单张图片

    Args:
        prompt: 图片提示词
        account: 账号标识
        page_type: 页面类型
        secure_1psid: Cookie值
        secure_1psidts: Cookie值

    Returns:
        图片保存路径
    """
    generator = GeminiAPIGenerator(secure_1psid, secure_1psidts)
    try:
        await generator.start()
        result = await generator.generate_image(prompt, account, page_type)
        return result
    finally:
        await generator.close()


# ========== 测试入口 ==========

if __name__ == "__main__":
    async def test():
        print("\n" + "=" * 60)
        print("Gemini API 图片生成器测试 (Gemini 3.0 Pro)")
        print("=" * 60)

        # 检查环境变量
        if not os.getenv("GEMINI_SECURE_1PSID"):
            print("\n[错误] 请先设置环境变量 GEMINI_SECURE_1PSID")
            print("\n获取方式：")
            print("1. 打开Chrome，登录 https://gemini.google.com")
            print("2. F12 -> Application -> Cookies -> gemini.google.com")
            print("3. 复制 __Secure-1PSID 的值")
            print("\n设置环境变量：")
            print('  Windows: set GEMINI_SECURE_1PSID=你的cookie值')
            print('  Linux/Mac: export GEMINI_SECURE_1PSID=你的cookie值')
            return

        # 使用默认Pro模型 (gemini-3.0-pro-exp)
        generator = GeminiAPIGenerator(model="pro")
        try:
            await generator.start()

            # 测试生成一张封面图（带中文文字）
            test_prompt = """【图片类型】小红书轮播图封面
【尺寸】竖版3:4（1080×1440px）

【画面风格】
极简奢华杂志封面设计。珍珠白到浅灰渐变背景(#F8F8F8 → #EEEEEE)，大面积留白60%+。

【核心内容】
- 主标题：「AI帮你日更不用愁」
  - 位置：画面正中央偏上
  - 字体：现代无衬线粗体，深灰色(#333333)
  - 大小：占画面宽度60-70%
  - 效果：清晰锐利，手机端一眼可读
- 副标题：「1天8条高质量脚本」
  - 位置：主标题正下方
  - 字体：现代无衬线细体，浅灰色(#666666)
  - 大小：主标题的40-50%

【装饰元素】
- 少量紫色(#7C3AED)发光线条或光点粒子
- 底部细线装饰
- 整体克制、高级、干净

【负面提示词】
文字变形扭曲、文字模糊不清、字体过小、人物、复杂背景、英文、水印、logo"""

            result = await generator.generate_image(test_prompt, "A", "封面-测试")

            print(f"\n测试结果: {result}")

        finally:
            await generator.close()

    asyncio.run(test())
