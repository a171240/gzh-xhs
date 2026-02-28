"""
小红书发布调度器
支持定时任务、账号轮转、频率限制
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass, asdict
import heapq

from account_manager import AccountManager, ACCOUNT_CONFIG
from xhs_publisher import XiaoHongShuPublisher, NoteData, PublishResult


# ========== 数据结构 ==========

@dataclass
class ScheduledTask:
    """定时任务"""
    task_id: str
    account_id: str
    note_data: dict  # NoteData的字典形式
    scheduled_time: str  # ISO格式时间
    status: str = "pending"  # pending / running / completed / failed
    result: Optional[dict] = None
    created_at: str = None
    completed_at: Optional[str] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()

    def __lt__(self, other):
        """用于优先队列排序"""
        return self.scheduled_time < other.scheduled_time


# ========== 调度器 ==========

class PublishScheduler:
    """发布调度器"""

    def __init__(self, base_dir: str = None):
        """
        初始化调度器

        Args:
            base_dir: 基础目录
        """
        if base_dir is None:
            base_dir = Path(__file__).parent

        self.base_dir = Path(base_dir)
        self.tasks_file = self.base_dir / "scheduled_tasks.json"

        self.manager = AccountManager(base_dir)
        self.publisher = XiaoHongShuPublisher(self.manager)

        # 任务队列（优先队列，按时间排序）
        self.task_queue: List[ScheduledTask] = []
        self.tasks: Dict[str, ScheduledTask] = {}

        # 加载已保存的任务
        self._load_tasks()

        # 调度器状态
        self.is_running = False
        self._scheduler_task = None

    def _load_tasks(self):
        """加载任务"""
        if self.tasks_file.exists():
            try:
                with open(self.tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for task_data in data:
                        task = ScheduledTask(**task_data)
                        self.tasks[task.task_id] = task
                        if task.status == "pending":
                            heapq.heappush(self.task_queue, task)
            except Exception as e:
                print(f"加载任务失败: {e}")

    def _save_tasks(self):
        """保存任务"""
        data = [asdict(task) for task in self.tasks.values()]
        with open(self.tasks_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _generate_task_id(self) -> str:
        """生成任务ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        count = len(self.tasks) + 1
        return f"task_{timestamp}_{count:04d}"

    # ========== 任务管理 ==========

    def add_task(
        self,
        account_id: str,
        note_data: NoteData,
        scheduled_time: datetime = None
    ) -> str:
        """
        添加发布任务

        Args:
            account_id: 账号标识
            note_data: 笔记数据
            scheduled_time: 计划发布时间（默认立即）

        Returns:
            任务ID
        """
        if scheduled_time is None:
            scheduled_time = datetime.now()

        task_id = self._generate_task_id()
        task = ScheduledTask(
            task_id=task_id,
            account_id=account_id,
            note_data=asdict(note_data) if hasattr(note_data, '__dataclass_fields__') else note_data,
            scheduled_time=scheduled_time.isoformat()
        )

        self.tasks[task_id] = task
        heapq.heappush(self.task_queue, task)
        self._save_tasks()

        print(f"[+] 任务已添加: {task_id}")
        print(f"    账号: {account_id}号")
        print(f"    时间: {scheduled_time.strftime('%Y-%m-%d %H:%M:%S')}")

        return task_id

    def add_batch_tasks(
        self,
        notes: List[Dict],
        start_time: datetime = None,
        interval_minutes: int = 60
    ) -> List[str]:
        """
        批量添加任务（自动错开时间）

        Args:
            notes: 笔记列表 [{"account_id": "A", "note_data": {...}}, ...]
            start_time: 第一篇的发布时间
            interval_minutes: 每篇间隔（分钟）

        Returns:
            任务ID列表
        """
        if start_time is None:
            start_time = datetime.now() + timedelta(minutes=5)

        task_ids = []
        current_time = start_time

        # 按账号分组，实现交替发布
        account_notes = {"A": [], "B": [], "C": []}
        for note in notes:
            account_id = note.get("account_id", "A")
            account_notes[account_id].append(note)

        # 轮转调度
        while any(account_notes.values()):
            for account_id in ["A", "B", "C"]:
                if account_notes[account_id]:
                    note = account_notes[account_id].pop(0)
                    note_data = note.get("note_data", {})

                    if isinstance(note_data, dict):
                        note_data = NoteData(**note_data)

                    task_id = self.add_task(
                        account_id=account_id,
                        note_data=note_data,
                        scheduled_time=current_time
                    )
                    task_ids.append(task_id)
                    current_time += timedelta(minutes=interval_minutes)

        return task_ids

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if task.status == "pending":
                task.status = "cancelled"
                self._save_tasks()
                print(f"[x] 任务已取消: {task_id}")
                return True
            else:
                print(f"[!] 任务无法取消（状态: {task.status}）")
        return False

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务详情"""
        return self.tasks.get(task_id)

    def list_tasks(self, status: str = None) -> List[ScheduledTask]:
        """
        列出任务

        Args:
            status: 筛选状态（pending/completed/failed/all）
        """
        tasks = list(self.tasks.values())
        if status and status != "all":
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.scheduled_time)

    def print_tasks(self, status: str = "pending"):
        """打印任务列表"""
        tasks = self.list_tasks(status)

        print(f"\n{'='*60}")
        print(f"发布任务列表 (状态: {status})")
        print(f"{'='*60}")

        if not tasks:
            print("  暂无任务")
            return

        for task in tasks:
            status_icon = {
                "pending": "[等待]",
                "running": "[执行中]",
                "completed": "[完成]",
                "failed": "[失败]",
                "cancelled": "[取消]"
            }.get(task.status, "[?]")

            print(f"\n{status_icon} {task.task_id}")
            print(f"  账号: {task.account_id}号")
            print(f"  标题: {task.note_data.get('title', '无标题')[:30]}")
            print(f"  计划时间: {task.scheduled_time}")
            if task.completed_at:
                print(f"  完成时间: {task.completed_at}")
            if task.result:
                print(f"  结果: {'成功' if task.result.get('success') else '失败'}")

        print(f"\n{'='*60}")

    # ========== 调度执行 ==========

    async def _execute_task(self, task: ScheduledTask) -> PublishResult:
        """执行单个任务"""
        task.status = "running"
        self._save_tasks()

        print(f"\n[>>] 开始执行任务: {task.task_id}")

        try:
            note_data = NoteData(**task.note_data)
            result = await self.publisher.publish(task.account_id, note_data)

            task.status = "completed" if result.success else "failed"
            task.result = asdict(result)
            task.completed_at = datetime.now().isoformat()

        except Exception as e:
            task.status = "failed"
            task.result = {"success": False, "error_message": str(e)}
            task.completed_at = datetime.now().isoformat()
            result = PublishResult(success=False, error_message=str(e))

        self._save_tasks()
        return result

    async def _scheduler_loop(self):
        """调度器主循环"""
        print("\n[调度器] 启动")

        while self.is_running:
            now = datetime.now()

            # 检查是否有到期的任务
            while self.task_queue:
                task = self.task_queue[0]  # 查看队首

                # 跳过已取消的任务
                if task.status != "pending":
                    heapq.heappop(self.task_queue)
                    continue

                scheduled_time = datetime.fromisoformat(task.scheduled_time)

                if scheduled_time <= now:
                    # 任务到期，执行
                    heapq.heappop(self.task_queue)
                    await self._execute_task(task)

                    # 执行后等待一段时间
                    await asyncio.sleep(5)
                else:
                    # 最近的任务还没到期
                    break

            # 每10秒检查一次
            await asyncio.sleep(10)

        print("[调度器] 已停止")

    def start(self):
        """启动调度器"""
        if self.is_running:
            print("[调度器] 已在运行中")
            return

        self.is_running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        print("[调度器] 已启动")

    async def stop(self):
        """停止调度器"""
        self.is_running = False
        if self._scheduler_task:
            await self._scheduler_task
        await self.publisher.close()
        print("[调度器] 已停止")

    # ========== 立即执行 ==========

    async def run_now(self, account_id: str, note_data: NoteData) -> PublishResult:
        """立即发布（不经过调度）"""
        return await self.publisher.publish(account_id, note_data)

    async def run_batch_now(self, notes: List[Dict]) -> List[PublishResult]:
        """立即批量发布"""
        return await self.publisher.batch_publish(notes)


# ========== 便捷函数 ==========

def create_30day_schedule(content_calendar: List[Dict]) -> List[Dict]:
    """
    根据30天内容日历创建发布计划

    Args:
        content_calendar: 内容日历
            [
                {
                    "day": 1,
                    "account": "A",
                    "topic": "AI选题工具",
                    "type": "问题-解决型"
                },
                ...
            ]

    Returns:
        发布任务列表
    """
    notes = []
    start_date = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

    for item in content_calendar:
        day = item.get("day", 1)
        account_id = item.get("account", "A")

        # 计算发布日期（每天上午10点）
        publish_date = start_date + timedelta(days=day - 1)

        notes.append({
            "account_id": account_id,
            "scheduled_time": publish_date.isoformat(),
            "note_data": {
                "title": item.get("title", f"Day{day}内容"),
                "content": item.get("content", ""),
                "images": item.get("images", []),
                "tags": item.get("tags", []),
            }
        })

    return notes


# ========== 测试入口 ==========

if __name__ == "__main__":
    async def test():
        scheduler = PublishScheduler()

        # 打印当前任务
        scheduler.print_tasks("all")

        # 测试添加任务
        test_note = NoteData(
            title="测试发布",
            content="这是一条测试内容",
            images=["test.png"],
            tags=["测试"]
        )

        # 添加一个5分钟后的任务
        # scheduler.add_task(
        #     account_id="A",
        #     note_data=test_note,
        #     scheduled_time=datetime.now() + timedelta(minutes=5)
        # )

        # 打印任务列表
        scheduler.print_tasks()

        await scheduler.stop()

    asyncio.run(test())
