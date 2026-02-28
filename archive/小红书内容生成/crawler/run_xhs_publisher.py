"""
小红书自动发布CLI入口
支持登录、发布、批量发布、定时发布
"""

import asyncio
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta

from account_manager import AccountManager, ACCOUNT_CONFIG
from xhs_publisher import XiaoHongShuPublisher, NoteData
from publish_scheduler import PublishScheduler


# ========== CLI命令 ==========

async def cmd_status():
    """查看账号状态"""
    manager = AccountManager()
    manager.print_status()


async def cmd_login(account_id: str):
    """登录指定账号"""
    manager = AccountManager()

    if account_id == "all":
        for acc_id in ["A", "B", "C"]:
            print(f"\n{'='*50}")
            await manager.login(acc_id)
    else:
        await manager.login(account_id)

    await manager.close_browser()


async def cmd_check(account_id: str):
    """检查登录状态"""
    manager = AccountManager()

    if account_id == "all":
        for acc_id in ["A", "B", "C"]:
            config = ACCOUNT_CONFIG[acc_id]
            is_logged_in = await manager.check_login_status(acc_id)
            status = "[OK] 已登录" if is_logged_in else "[X] 未登录"
            print(f"{acc_id}号 ({config['name']}): {status}")
    else:
        config = ACCOUNT_CONFIG[account_id]
        is_logged_in = await manager.check_login_status(account_id)
        status = "[OK] 已登录" if is_logged_in else "[X] 未登录"
        print(f"{account_id}号 ({config['name']}): {status}")

    await manager.close_browser()


async def cmd_publish(account_id: str, content_file: str):
    """发布笔记"""
    # 读取内容文件
    content_path = Path(content_file)
    if not content_path.exists():
        print(f"[错误] 内容文件不存在: {content_file}")
        return

    with open(content_path, 'r', encoding='utf-8') as f:
        content = json.load(f)

    note_data = NoteData(
        title=content.get("title", ""),
        content=content.get("content", ""),
        images=content.get("images", []),
        tags=content.get("tags", []),
        topics=content.get("topics", [])
    )

    publisher = XiaoHongShuPublisher()
    result = await publisher.publish(account_id, note_data)

    if result.success:
        print(f"\n[OK] 发布成功！")
        if result.note_url:
            print(f"笔记链接: {result.note_url}")
    else:
        print(f"\n[FAIL] 发布失败: {result.error_message}")

    await publisher.close()


async def cmd_batch(input_file: str, interval: int = 60):
    """批量发布"""
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"[错误] 输入文件不存在: {input_file}")
        return

    with open(input_path, 'r', encoding='utf-8') as f:
        notes = json.load(f)

    publisher = XiaoHongShuPublisher()
    results = await publisher.batch_publish(notes)

    # 打印结果汇总
    success_count = sum(1 for r in results if r.success)
    print(f"\n批量发布完成: {success_count}/{len(results)} 成功")

    await publisher.close()


async def cmd_schedule(account_id: str, content_file: str, time_str: str):
    """定时发布"""
    # 解析时间
    try:
        scheduled_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
    except ValueError:
        print(f"[错误] 时间格式错误，应为: YYYY-MM-DD HH:MM")
        return

    # 读取内容
    content_path = Path(content_file)
    if not content_path.exists():
        print(f"[错误] 内容文件不存在: {content_file}")
        return

    with open(content_path, 'r', encoding='utf-8') as f:
        content = json.load(f)

    note_data = NoteData(
        title=content.get("title", ""),
        content=content.get("content", ""),
        images=content.get("images", []),
        tags=content.get("tags", [])
    )

    scheduler = PublishScheduler()
    task_id = scheduler.add_task(
        account_id=account_id,
        note_data=note_data,
        scheduled_time=scheduled_time
    )

    print(f"\n[OK] 定时任务已创建: {task_id}")
    print(f"计划发布时间: {scheduled_time.strftime('%Y-%m-%d %H:%M')}")

    await scheduler.stop()


async def cmd_tasks(status: str = "pending"):
    """查看任务列表"""
    scheduler = PublishScheduler()
    scheduler.print_tasks(status)
    await scheduler.stop()


async def cmd_run_scheduler():
    """运行调度器"""
    scheduler = PublishScheduler()

    print("\n" + "=" * 60)
    print("小红书发布调度器")
    print("=" * 60)
    print("按 Ctrl+C 停止")
    print("=" * 60)

    scheduler.start()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n收到停止信号...")
        await scheduler.stop()


async def cmd_test(account_id: str):
    """测试模式：打开创作中心但不发布"""
    publisher = XiaoHongShuPublisher()

    # 检查登录
    if not await publisher.check_and_login(account_id):
        print("[错误] 登录失败")
        await publisher.close()
        return

    # 打开创作中心
    page = await publisher.manager.get_page(account_id)
    await page.goto("https://creator.xiaohongshu.com/publish/publish")
    await page.wait_for_load_state("networkidle")

    print("\n[OK] 已打开创作中心")
    print("请在浏览器中手动操作测试")
    print("按回车关闭浏览器...")
    input()

    await publisher.close()


# ========== 主入口 ==========

def main():
    parser = argparse.ArgumentParser(
        description="小红书自动发布工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 查看账号状态
  python run_publisher.py status

  # 登录A号
  python run_publisher.py login -a A

  # 登录所有账号
  python run_publisher.py login -a all

  # 检查登录状态
  python run_publisher.py check -a all

  # 发布笔记
  python run_publisher.py publish -a A -c content.json

  # 批量发布
  python run_publisher.py batch -i batch_notes.json

  # 定时发布
  python run_publisher.py schedule -a A -c content.json -t "2026-01-12 10:00"

  # 查看任务列表
  python run_publisher.py tasks

  # 启动调度器
  python run_publisher.py run

  # 测试模式
  python run_publisher.py test -a A
"""
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # status 命令
    subparsers.add_parser("status", help="查看账号状态")

    # login 命令
    login_parser = subparsers.add_parser("login", help="登录账号")
    login_parser.add_argument(
        "-a", "--account",
        type=str,
        default="A",
        choices=["A", "B", "C", "all"],
        help="账号标识 (A/B/C/all)"
    )

    # check 命令
    check_parser = subparsers.add_parser("check", help="检查登录状态")
    check_parser.add_argument(
        "-a", "--account",
        type=str,
        default="all",
        choices=["A", "B", "C", "all"],
        help="账号标识"
    )

    # publish 命令
    publish_parser = subparsers.add_parser("publish", help="发布笔记")
    publish_parser.add_argument(
        "-a", "--account",
        type=str,
        default="A",
        choices=["A", "B", "C"],
        help="账号标识"
    )
    publish_parser.add_argument(
        "-c", "--content",
        type=str,
        required=True,
        help="内容文件路径 (JSON格式)"
    )

    # batch 命令
    batch_parser = subparsers.add_parser("batch", help="批量发布")
    batch_parser.add_argument(
        "-i", "--input",
        type=str,
        required=True,
        help="输入文件路径 (JSON格式)"
    )
    batch_parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="发布间隔（分钟）"
    )

    # schedule 命令
    schedule_parser = subparsers.add_parser("schedule", help="定时发布")
    schedule_parser.add_argument(
        "-a", "--account",
        type=str,
        default="A",
        choices=["A", "B", "C"],
        help="账号标识"
    )
    schedule_parser.add_argument(
        "-c", "--content",
        type=str,
        required=True,
        help="内容文件路径"
    )
    schedule_parser.add_argument(
        "-t", "--time",
        type=str,
        required=True,
        help="发布时间 (YYYY-MM-DD HH:MM)"
    )

    # tasks 命令
    tasks_parser = subparsers.add_parser("tasks", help="查看任务列表")
    tasks_parser.add_argument(
        "-s", "--status",
        type=str,
        default="pending",
        choices=["pending", "completed", "failed", "all"],
        help="筛选状态"
    )

    # run 命令
    subparsers.add_parser("run", help="启动调度器")

    # test 命令
    test_parser = subparsers.add_parser("test", help="测试模式")
    test_parser.add_argument(
        "-a", "--account",
        type=str,
        default="A",
        choices=["A", "B", "C"],
        help="账号标识"
    )

    args = parser.parse_args()

    # 执行命令
    if args.command == "status":
        asyncio.run(cmd_status())
    elif args.command == "login":
        asyncio.run(cmd_login(args.account))
    elif args.command == "check":
        asyncio.run(cmd_check(args.account))
    elif args.command == "publish":
        asyncio.run(cmd_publish(args.account, args.content))
    elif args.command == "batch":
        asyncio.run(cmd_batch(args.input, args.interval))
    elif args.command == "schedule":
        asyncio.run(cmd_schedule(args.account, args.content, args.time))
    elif args.command == "tasks":
        asyncio.run(cmd_tasks(args.status))
    elif args.command == "run":
        asyncio.run(cmd_run_scheduler())
    elif args.command == "test":
        asyncio.run(cmd_test(args.account))
    else:
        parser.print_help()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("小红书自动发布工具")
    print("=" * 60)
    main()
