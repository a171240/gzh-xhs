"""
小红书爬虫模块

提供小红书内容爬取和存储功能：
- XHSCrawler: 小红书爬虫封装
- FeishuStorage: 飞书多维表格存储
- LocalStorage: 本地CSV存储

使用方法：
    from crawler import XHSCrawler, LocalStorage

    # 初始化爬虫
    crawler = XHSCrawler()

    # 搜索关键词
    results = crawler.batch_search(["AI选题工具", "内容中台"])

    # 保存到本地
    storage = LocalStorage()
    storage.save_records(results)
"""

from .xhs_crawler import XHSCrawler
from .feishu_storage import FeishuStorage
from .local_storage import LocalStorage
from .config import CRAWLER_CONFIG, STORAGE_CONFIG

__version__ = "1.0.0"
__all__ = [
    "XHSCrawler",
    "FeishuStorage",
    "LocalStorage",
    "CRAWLER_CONFIG",
    "STORAGE_CONFIG",
]
