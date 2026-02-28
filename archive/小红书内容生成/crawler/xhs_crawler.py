"""
小红书爬虫封装模块
基于 MediaCrawler 实现关键词搜索和数据采集

使用前需先安装 MediaCrawler:
  git clone https://github.com/NanmiCoder/MediaCrawler.git
  cd MediaCrawler && pip install -r requirements.txt
  playwright install
"""
import subprocess
import json
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from config import CRAWLER_CONFIG, FIELD_MAPPING


class XHSCrawler:
    """小红书爬虫封装类"""

    def __init__(self):
        self.config = CRAWLER_CONFIG
        self.media_crawler_path = Path(self.config["media_crawler_path"])

    def check_installation(self) -> bool:
        """检查 MediaCrawler 是否已安装"""
        main_py = self.media_crawler_path / "main.py"
        if not main_py.exists():
            print(f"[错误] MediaCrawler 未安装在: {self.media_crawler_path}")
            print("请运行: git clone https://github.com/NanmiCoder/MediaCrawler.git")
            return False
        return True

    def search_by_keyword(self, keyword: str, pages: int = 5) -> List[Dict]:
        """
        按关键词搜索小红书笔记

        Args:
            keyword: 搜索关键词
            pages: 爬取页数

        Returns:
            笔记列表
        """
        if not self.check_installation():
            return []

        print(f"[搜索] 关键词: {keyword}, 页数: {pages}")

        # 构建命令
        cmd = [
            sys.executable, "main.py",
            "--platform", "xhs",
            "--lt", self.config["login_type"],
            "--type", "search",
        ]

        # 设置搜索关键词（通过配置文件或环境变量）
        # MediaCrawler 默认从配置文件读取关键词
        # 这里我们需要临时修改配置或使用API方式

        try:
            # 运行 MediaCrawler
            result = subprocess.run(
                cmd,
                cwd=str(self.media_crawler_path),
                capture_output=True,
                text=True,
                timeout=300  # 5分钟超时
            )

            if result.returncode != 0:
                print(f"[错误] 爬虫执行失败: {result.stderr}")
                return []

        except subprocess.TimeoutExpired:
            print("[错误] 爬虫执行超时")
            return []
        except Exception as e:
            print(f"[错误] 爬虫执行异常: {e}")
            return []

        # 解析结果
        return self._parse_results(keyword)

    def batch_search(self, keywords: Optional[List[str]] = None) -> List[Dict]:
        """
        批量关键词搜索

        Args:
            keywords: 关键词列表，为空则使用配置中的关键词

        Returns:
            所有笔记列表
        """
        if keywords is None:
            keywords = self.config["keywords"]

        all_results = []
        total = len(keywords)

        for i, keyword in enumerate(keywords, 1):
            print(f"\n[进度] {i}/{total} - 正在搜索: {keyword}")
            results = self.search_by_keyword(
                keyword,
                self.config["pages_per_keyword"]
            )

            # 添加关键词标记
            for r in results:
                r["keyword"] = keyword
                r["crawl_time"] = datetime.now().isoformat()

            all_results.extend(results)
            print(f"[结果] 获取 {len(results)} 条笔记")

        return all_results

    def _parse_results(self, keyword: str) -> List[Dict]:
        """
        解析 MediaCrawler 输出结果

        Args:
            keyword: 搜索关键词

        Returns:
            笔记列表
        """
        # MediaCrawler 输出目录
        output_dir = self.media_crawler_path / "data" / "xhs"
        results = []

        if not output_dir.exists():
            print(f"[警告] 输出目录不存在: {output_dir}")
            return results

        # 查找最新的CSV文件
        csv_files = sorted(
            output_dir.glob("*.csv"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        if not csv_files:
            print("[警告] 未找到CSV输出文件")
            return results

        # 读取最新的文件
        latest_file = csv_files[0]
        print(f"[读取] 解析文件: {latest_file.name}")

        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 添加笔记链接
                    if "note_id" in row:
                        row["note_url"] = f"https://www.xiaohongshu.com/explore/{row['note_id']}"
                    results.append(dict(row))
        except Exception as e:
            print(f"[错误] 解析CSV失败: {e}")

        return results

    def read_existing_data(self, filepath: str) -> List[Dict]:
        """
        读取已有的爬取数据

        Args:
            filepath: CSV文件路径

        Returns:
            笔记列表
        """
        results = []
        path = Path(filepath)

        if not path.exists():
            print(f"[警告] 文件不存在: {filepath}")
            return results

        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    results.append(dict(row))
        except Exception as e:
            print(f"[错误] 读取文件失败: {e}")

        return results


class XHSDirectCrawler:
    """
    直接使用 MediaCrawler API 的爬虫类
    （不通过命令行调用，而是直接导入使用）
    """

    def __init__(self):
        self.config = CRAWLER_CONFIG

    def search(self, keyword: str, limit: int = 100) -> List[Dict]:
        """
        直接搜索（需要 MediaCrawler 在 Python 路径中）

        注意：这种方式需要更复杂的设置，建议先使用命令行方式
        """
        try:
            # 尝试导入 MediaCrawler
            sys.path.insert(0, str(Path(self.config["media_crawler_path"])))

            # 这里需要根据 MediaCrawler 的实际 API 来实现
            # 由于 MediaCrawler 主要是命令行工具，直接 API 调用可能需要额外配置

            print("[提示] 直接 API 调用模式暂未实现，请使用命令行方式")
            return []

        except ImportError as e:
            print(f"[错误] 无法导入 MediaCrawler: {e}")
            return []


if __name__ == "__main__":
    # 测试爬虫
    crawler = XHSCrawler()

    # 检查安装
    if crawler.check_installation():
        print("MediaCrawler 已安装")

        # 测试单个关键词搜索
        # results = crawler.search_by_keyword("AI选题工具", pages=1)
        # print(f"获取 {len(results)} 条结果")
    else:
        print("请先安装 MediaCrawler")
