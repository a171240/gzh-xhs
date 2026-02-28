"""
小红书多账号管理模块
管理3个独立浏览器Profile，实现账号隔离
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass, field, asdict

# Playwright导入
try:
    from playwright.async_api import async_playwright, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("请安装playwright: pip install playwright && playwright install chromium")


# ========== 账号配置 ==========

ACCOUNT_CONFIG = {
    "A": {
        "name": "转化号",
        "profile_dir": "browser_profiles/account_A",
        "daily_limit": 6,       # 每日发布上限
        "min_interval": 3600,   # 最小发布间隔（秒）
        "color": "#7C3AED",     # 紫色
        "badge": "诊断",
    },
    "B": {
        "name": "交付号",
        "profile_dir": "browser_profiles/account_B",
        "daily_limit": 4,
        "min_interval": 3600,
        "color": "#6B7280",     # 灰色
        "badge": "SOP",
    },
    "C": {
        "name": "观点号",
        "profile_dir": "browser_profiles/account_C",
        "daily_limit": 3,
        "min_interval": 3600,
        "color": "#374151",     # 深灰
        "badge": "观点",
    }
}


# ========== 数据结构 ==========

@dataclass
class AccountState:
    """账号状态"""
    account_id: str
    name: str
    is_logged_in: bool = False
    last_login_check: Optional[str] = None
    last_publish_time: Optional[str] = None
    today_publish_count: int = 0
    total_publish_count: int = 0
    error_count: int = 0
    last_error: Optional[str] = None


@dataclass
class PublishRecord:
    """发布记录"""
    account_id: str
    note_id: Optional[str] = None
    title: str = ""
    publish_time: str = ""
    success: bool = False
    error_message: Optional[str] = None


# ========== 账号管理器 ==========

class AccountManager:
    """小红书多账号管理器"""

    def __init__(self, base_dir: str = None):
        """
        初始化账号管理器

        Args:
            base_dir: 基础目录，默认为crawler目录
        """
        if base_dir is None:
            base_dir = Path(__file__).parent

        self.base_dir = Path(base_dir)
        self.profiles_dir = self.base_dir / "browser_profiles"
        self.state_file = self.base_dir / "account_states.json"
        self.history_file = self.base_dir / "publish_history.json"

        # 确保目录存在
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        for account_id in ACCOUNT_CONFIG:
            profile_dir = self.profiles_dir / f"account_{account_id}"
            profile_dir.mkdir(parents=True, exist_ok=True)

        # 加载状态
        self.states: Dict[str, AccountState] = self._load_states()
        self.history: List[PublishRecord] = self._load_history()

        # 浏览器上下文缓存
        self._contexts: Dict[str, BrowserContext] = {}
        self._playwright = None

    def _load_states(self) -> Dict[str, AccountState]:
        """加载账号状态"""
        states = {}

        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for account_id, state_data in data.items():
                        states[account_id] = AccountState(**state_data)
            except Exception as e:
                print(f"加载账号状态失败: {e}")

        # 确保所有账号都有状态
        for account_id, config in ACCOUNT_CONFIG.items():
            if account_id not in states:
                states[account_id] = AccountState(
                    account_id=account_id,
                    name=config["name"]
                )

        return states

    def _save_states(self):
        """保存账号状态"""
        data = {k: asdict(v) for k, v in self.states.items()}
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_history(self) -> List[PublishRecord]:
        """加载发布历史"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return [PublishRecord(**record) for record in data]
            except Exception as e:
                print(f"加载发布历史失败: {e}")
        return []

    def _save_history(self):
        """保存发布历史"""
        data = [asdict(record) for record in self.history[-1000:]]  # 只保留最近1000条
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_account_config(self, account_id: str) -> dict:
        """获取账号配置"""
        if account_id not in ACCOUNT_CONFIG:
            raise ValueError(f"未知账号: {account_id}，可用账号: A/B/C")
        return ACCOUNT_CONFIG[account_id]

    def get_profile_dir(self, account_id: str) -> Path:
        """获取账号的浏览器Profile目录"""
        config = self.get_account_config(account_id)
        return self.base_dir / config["profile_dir"]

    def get_state(self, account_id: str) -> AccountState:
        """获取账号状态"""
        if account_id not in self.states:
            config = self.get_account_config(account_id)
            self.states[account_id] = AccountState(
                account_id=account_id,
                name=config["name"]
            )
        return self.states[account_id]

    def can_publish(self, account_id: str) -> tuple[bool, str]:
        """
        检查账号是否可以发布

        Returns:
            (是否可发布, 原因说明)
        """
        config = self.get_account_config(account_id)
        state = self.get_state(account_id)

        # 检查登录状态
        if not state.is_logged_in:
            return False, "账号未登录，请先执行登录"

        # 检查每日限额
        today = datetime.now().strftime("%Y-%m-%d")
        if state.last_publish_time and state.last_publish_time.startswith(today):
            if state.today_publish_count >= config["daily_limit"]:
                return False, f"已达每日发布上限 ({config['daily_limit']}篇)"

        # 检查发布间隔
        if state.last_publish_time:
            last_time = datetime.fromisoformat(state.last_publish_time)
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < config["min_interval"]:
                wait_minutes = int((config["min_interval"] - elapsed) / 60)
                return False, f"距上次发布时间不足，请等待 {wait_minutes} 分钟"

        return True, "可以发布"

    def record_publish(self, account_id: str, success: bool,
                      note_id: str = None, title: str = "",
                      error_message: str = None):
        """记录发布结果"""
        state = self.get_state(account_id)
        now = datetime.now().isoformat()

        # 更新状态
        state.last_publish_time = now

        today = datetime.now().strftime("%Y-%m-%d")
        if not state.last_publish_time or not state.last_publish_time.startswith(today):
            state.today_publish_count = 0

        if success:
            state.today_publish_count += 1
            state.total_publish_count += 1
        else:
            state.error_count += 1
            state.last_error = error_message

        # 添加历史记录
        record = PublishRecord(
            account_id=account_id,
            note_id=note_id,
            title=title,
            publish_time=now,
            success=success,
            error_message=error_message
        )
        self.history.append(record)

        # 保存
        self._save_states()
        self._save_history()

    def reset_daily_count(self, account_id: str = None):
        """重置每日计数（通常在午夜自动调用）"""
        if account_id:
            if account_id in self.states:
                self.states[account_id].today_publish_count = 0
        else:
            for state in self.states.values():
                state.today_publish_count = 0
        self._save_states()

    # ========== 浏览器管理 ==========

    async def _ensure_playwright(self):
        """确保Playwright已启动"""
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def get_browser_context(self, account_id: str) -> BrowserContext:
        """
        获取账号的浏览器上下文（复用或创建）

        Args:
            account_id: 账号标识 (A/B/C)

        Returns:
            BrowserContext实例
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright未安装")

        # 复用已有上下文
        if account_id in self._contexts:
            return self._contexts[account_id]

        await self._ensure_playwright()

        profile_dir = self.get_profile_dir(account_id)
        config = self.get_account_config(account_id)

        print(f"启动浏览器: {config['name']} ({account_id}号)")

        # 使用persistent context保持登录状态
        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,  # 显示浏览器窗口
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=[
                "--disable-blink-features=AutomationControlled",  # 隐藏自动化特征
            ]
        )

        self._contexts[account_id] = context
        return context

    async def get_page(self, account_id: str) -> Page:
        """获取账号的页面"""
        context = await self.get_browser_context(account_id)
        pages = context.pages
        if pages:
            return pages[0]
        return await context.new_page()

    async def check_login_status(self, account_id: str) -> bool:
        """
        检查账号登录状态

        Returns:
            是否已登录
        """
        try:
            page = await self.get_page(account_id)
            await page.goto("https://www.xiaohongshu.com/user/profile/self", timeout=15000)
            await page.wait_for_load_state("networkidle", timeout=10000)

            # 检查是否有登录按钮（未登录）
            login_button = await page.query_selector('text=登录')
            is_logged_in = login_button is None

            # 更新状态
            state = self.get_state(account_id)
            state.is_logged_in = is_logged_in
            state.last_login_check = datetime.now().isoformat()
            self._save_states()

            return is_logged_in

        except Exception as e:
            print(f"检查登录状态失败: {e}")
            return False

    async def login(self, account_id: str, auto_wait: bool = True) -> bool:
        """
        执行登录（弹出登录页面，自动等待登录完成）

        Args:
            account_id: 账号ID
            auto_wait: 是否自动等待登录完成（默认True）

        Returns:
            是否登录成功
        """
        config = self.get_account_config(account_id)
        print(f"\n{'='*50}")
        print(f"正在登录: {config['name']} ({account_id}号)")
        print(f"{'='*50}")

        page = await self.get_page(account_id)

        # 打开登录页面
        print("正在打开小红书...")
        try:
            await page.goto("https://www.xiaohongshu.com/explore", timeout=60000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"页面加载提示: {e}")

        # 点击登录按钮
        login_button = await page.query_selector('text=登录')
        if login_button:
            await login_button.click()
            await page.wait_for_timeout(1000)
            print("已弹出登录框，请扫码或验证...")

        if auto_wait:
            # 自动等待登录完成（最长3分钟）
            print("自动检测登录状态中...")
            for i in range(36):  # 36 * 5秒 = 3分钟
                await page.wait_for_timeout(5000)

                # 检查是否已登录（登录按钮消失）
                login_btn = await page.query_selector('text=登录')
                if not login_btn:
                    print(f"\n[OK] {config['name']} 登录成功！")
                    state = self.get_state(account_id)
                    state.is_logged_in = True
                    state.last_login_check = datetime.now().isoformat()
                    self._save_states()
                    return True

                if i % 6 == 0:  # 每30秒提示
                    print(f"  等待登录... ({(i+1)*5}秒)")

            print("\n[WARN] 登录超时（3分钟）")
            return False
        else:
            # 不等待，直接标记为已登录
            state = self.get_state(account_id)
            state.is_logged_in = True
            state.last_login_check = datetime.now().isoformat()
            self._save_states()
            print(f"\n[OK] {config['name']} 登录状态已保存！")
            return True

    async def close_browser(self, account_id: str = None):
        """关闭浏览器"""
        if account_id:
            if account_id in self._contexts:
                await self._contexts[account_id].close()
                del self._contexts[account_id]
        else:
            for context in self._contexts.values():
                await context.close()
            self._contexts.clear()

        if not self._contexts and self._playwright:
            await self._playwright.stop()
            self._playwright = None

    # ========== 账号轮转 ==========

    def get_next_available_account(self) -> Optional[str]:
        """
        获取下一个可用账号（用于轮转发布）

        Returns:
            账号ID或None（如果全部不可用）
        """
        # 按优先级排序：A > B > C
        for account_id in ["A", "B", "C"]:
            can_publish, reason = self.can_publish(account_id)
            if can_publish:
                return account_id

        return None

    def get_all_status(self) -> Dict[str, dict]:
        """获取所有账号状态汇总"""
        result = {}
        for account_id in ACCOUNT_CONFIG:
            config = self.get_account_config(account_id)
            state = self.get_state(account_id)
            can_publish, reason = self.can_publish(account_id)

            result[account_id] = {
                "name": config["name"],
                "is_logged_in": state.is_logged_in,
                "today_count": state.today_publish_count,
                "daily_limit": config["daily_limit"],
                "total_count": state.total_publish_count,
                "can_publish": can_publish,
                "status_reason": reason,
                "last_publish": state.last_publish_time,
            }

        return result

    def print_status(self):
        """打印账号状态"""
        print("\n" + "=" * 60)
        print("小红书账号状态")
        print("=" * 60)

        for account_id, status in self.get_all_status().items():
            login_icon = "[OK]" if status["is_logged_in"] else "[X]"
            publish_icon = "[OK]" if status["can_publish"] else "[X]"

            print(f"\n{account_id}号 - {status['name']}")
            print(f"  登录状态: {login_icon}")
            print(f"  今日发布: {status['today_count']}/{status['daily_limit']}")
            print(f"  总发布量: {status['total_count']}")
            print(f"  可否发布: {publish_icon} {status['status_reason']}")
            if status["last_publish"]:
                print(f"  上次发布: {status['last_publish']}")

        print("\n" + "=" * 60)


# ========== 测试入口 ==========

if __name__ == "__main__":
    async def test():
        manager = AccountManager()

        # 打印当前状态
        manager.print_status()

        # 测试登录A号
        print("\n测试登录A号...")
        # await manager.login("A")

        # 关闭
        await manager.close_browser()

    asyncio.run(test())
