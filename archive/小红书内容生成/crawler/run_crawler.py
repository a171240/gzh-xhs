"""
主执行脚本 - 小红书对标内容爬取与存储

功能：
1. 按配置的关键词批量搜索小红书笔记
2. 将结果存储到飞书多维表格
3. 同时保存本地CSV备份

使用方法：
  python run_crawler.py              # 使用配置文件中的关键词
  python run_crawler.py --keyword "AI选题"  # 搜索指定关键词
  python run_crawler.py --local      # 只保存到本地
  python run_crawler.py --feishu     # 只保存到飞书
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import CRAWLER_CONFIG, STORAGE_CONFIG, validate_config
from xhs_crawler import XHSCrawler
from feishu_storage import FeishuStorage
from local_storage import LocalStorage


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="小红书对标内容爬取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_crawler.py                    # 批量搜索配置中的所有关键词
  python run_crawler.py -k "AI选题"        # 搜索单个关键词
  python run_crawler.py -k "AI选题" -k "内容中台"  # 搜索多个关键词
  python run_crawler.py --local            # 只保存到本地CSV
  python run_crawler.py --feishu           # 只保存到飞书
  python run_crawler.py --pages 3          # 每个关键词爬取3页
        """
    )

    parser.add_argument(
        "-k", "--keyword",
        action="append",
        help="搜索关键词（可多次指定）"
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=CRAWLER_CONFIG["pages_per_keyword"],
        help=f"每个关键词爬取页数（默认: {CRAWLER_CONFIG['pages_per_keyword']}）"
    )

    parser.add_argument(
        "--local",
        action="store_true",
        help="只保存到本地CSV"
    )

    parser.add_argument(
        "--feishu",
        action="store_true",
        help="只保存到飞书多维表格"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="测试模式，不实际爬取"
    )

    return parser.parse_args()


def print_banner():
    """打印欢迎信息"""
    print("""
╔═══════════════════════════════════════════════════════════╗
║          小红书对标内容爬取工具 v1.0                      ║
║          基于 MediaCrawler 开源项目                        ║
╚═══════════════════════════════════════════════════════════╝
    """)


def main():
    """主函数"""
    print_banner()
    args = parse_args()

    # 确定存储模式
    if args.local:
        storage_mode = "local"
    elif args.feishu:
        storage_mode = "feishu"
    else:
        storage_mode = STORAGE_CONFIG["mode"]

    print(f"[配置] 存储模式: {storage_mode}")

    # 验证配置
    errors = validate_config()
    if storage_mode in ["feishu", "both"] and any("飞书" in e for e in errors):
        print("\n[警告] 飞书配置不完整:")
        for e in errors:
            if "飞书" in e:
                print(f"  - {e}")
        if storage_mode == "feishu":
            print("\n请配置 .env 文件后重试")
            return

    # 确定关键词
    if args.keyword:
        keywords = args.keyword
    else:
        keywords = CRAWLER_CONFIG["keywords"]

    print(f"[配置] 关键词数量: {len(keywords)}")
    print(f"[配置] 每关键词页数: {args.pages}")

    # 测试模式
    if args.dry_run:
        print("\n[测试模式] 将搜索以下关键词:")
        for kw in keywords:
            print(f"  - {kw}")
        print("\n使用 --help 查看更多选项")
        return

    # 1. 初始化爬虫
    crawler = XHSCrawler()

    if not crawler.check_installation():
        print("\n[错误] MediaCrawler 未安装")
        print("请运行以下命令安装:")
        print("  git clone https://github.com/NanmiCoder/MediaCrawler.git")
        print("  cd MediaCrawler && pip install -r requirements.txt")
        print("  playwright install")
        return

    # 2. 执行批量爬取
    print("\n" + "="*50)
    print("开始爬取小红书对标内容...")
    print("="*50)

    start_time = datetime.now()

    # 更新配置中的页数
    CRAWLER_CONFIG["pages_per_keyword"] = args.pages

    results = crawler.batch_search(keywords)

    elapsed = datetime.now() - start_time
    print(f"\n[统计] 共爬取 {len(results)} 条笔记")
    print(f"[统计] 耗时: {elapsed.total_seconds():.1f} 秒")

    if not results:
        print("[提示] 未获取到数据，请检查:")
        print("  1. MediaCrawler 是否正确安装")
        print("  2. 是否已扫码登录小红书")
        print("  3. 网络连接是否正常")
        return

    # 3. 存储数据
    print("\n" + "="*50)
    print("开始存储数据...")
    print("="*50)

    # 飞书存储
    if storage_mode in ["feishu", "both"]:
        print("\n[存储] 写入飞书多维表格...")
        feishu = FeishuStorage()
        if feishu.is_available():
            if feishu.save_records(results):
                print("[飞书] 存储完成")
            else:
                print("[飞书] 存储失败")
        else:
            print("[飞书] 存储不可用，跳过")

    # 本地存储
    if storage_mode in ["local", "both"]:
        print("\n[存储] 保存到本地CSV...")
        local = LocalStorage()
        # 使用第一个关键词作为文件名前缀
        keyword_prefix = keywords[0] if len(keywords) == 1 else "batch"
        filepath = local.save_records(results, keyword=keyword_prefix)
        if filepath:
            print(f"[本地] 存储完成: {filepath}")

    # 4. 完成
    print("\n" + "="*50)
    print("爬取任务完成！")
    print("="*50)

    # 打印摘要
    print(f"\n📊 结果摘要:")
    print(f"   - 搜索关键词: {len(keywords)} 个")
    print(f"   - 获取笔记: {len(results)} 条")
    print(f"   - 存储模式: {storage_mode}")
    print(f"   - 耗时: {elapsed.total_seconds():.1f} 秒")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[中断] 用户取消操作")
    except Exception as e:
        print(f"\n[错误] 程序异常: {e}")
        raise
