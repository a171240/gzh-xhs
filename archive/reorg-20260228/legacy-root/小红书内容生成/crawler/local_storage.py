"""
本地CSV存储模块
将爬取的数据保存到本地CSV文件

特点：
- 按关键词+日期命名文件
- 使用UTF-8-BOM编码（Excel兼容）
- 支持追加写入
- 自动创建目录
"""
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from config import STORAGE_CONFIG, FIELD_MAPPING


class LocalStorage:
    """本地CSV存储类"""

    def __init__(self):
        self.base_path = Path(STORAGE_CONFIG["local"]["base_path"])
        self._ensure_directory()

    def _ensure_directory(self):
        """确保存储目录存在"""
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_records(
        self,
        records: List[Dict],
        keyword: str = "all",
        filename: Optional[str] = None
    ) -> Path:
        """
        保存记录到本地CSV

        Args:
            records: 笔记记录列表
            keyword: 搜索关键词（用于文件命名）
            filename: 自定义文件名（可选）

        Returns:
            保存的文件路径
        """
        if not records:
            print("[提示] 没有记录需要保存")
            return None

        # 生成文件名
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 清理关键词中的特殊字符
            safe_keyword = "".join(c for c in keyword if c.isalnum() or c in "._- ")
            filename = f"{safe_keyword}_{timestamp}.csv"

        filepath = self.base_path / filename

        # 使用中文字段名
        fieldnames = list(FIELD_MAPPING.values())

        # 写入CSV
        try:
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for record in records:
                    row = self._format_record(record)
                    writer.writerow(row)

            print(f"[保存] 本地文件: {filepath}")
            print(f"[统计] 共 {len(records)} 条记录")
            return filepath

        except Exception as e:
            print(f"[错误] 保存文件失败: {e}")
            return None

    def _format_record(self, record: Dict) -> Dict:
        """
        格式化单条记录，将英文字段名转换为中文

        Args:
            record: 原始记录

        Returns:
            格式化后的记录
        """
        row = {}
        for eng_key, cn_key in FIELD_MAPPING.items():
            row[cn_key] = record.get(eng_key, "")
        return row

    def append_records(
        self,
        records: List[Dict],
        filepath: str
    ) -> bool:
        """
        追加记录到已有CSV文件

        Args:
            records: 笔记记录列表
            filepath: CSV文件路径

        Returns:
            是否成功
        """
        path = Path(filepath)

        if not path.exists():
            print(f"[警告] 文件不存在: {filepath}")
            return self.save_records(records, filename=path.name) is not None

        try:
            # 读取现有记录的笔记ID（用于去重）
            existing_ids = set()
            with open(path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if "笔记ID" in row:
                        existing_ids.add(row["笔记ID"])

            # 过滤重复记录
            new_records = []
            for record in records:
                note_id = record.get("note_id", "")
                if note_id and note_id not in existing_ids:
                    new_records.append(record)

            if not new_records:
                print("[提示] 没有新记录需要追加")
                return True

            # 追加写入
            fieldnames = list(FIELD_MAPPING.values())
            with open(path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                for record in new_records:
                    row = self._format_record(record)
                    writer.writerow(row)

            print(f"[追加] {len(new_records)} 条新记录到 {path.name}")
            return True

        except Exception as e:
            print(f"[错误] 追加记录失败: {e}")
            return False

    def list_files(self, pattern: str = "*.csv") -> List[Path]:
        """
        列出存储目录中的文件

        Args:
            pattern: 文件匹配模式

        Returns:
            文件路径列表
        """
        return sorted(
            self.base_path.glob(pattern),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

    def read_file(self, filepath: str) -> List[Dict]:
        """
        读取CSV文件

        Args:
            filepath: 文件路径

        Returns:
            记录列表
        """
        path = Path(filepath)
        records = []

        if not path.exists():
            print(f"[警告] 文件不存在: {filepath}")
            return records

        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    records.append(dict(row))
        except Exception as e:
            print(f"[错误] 读取文件失败: {e}")

        return records

    def save_as_json(self, records: List[Dict], filename: str) -> Path:
        """
        保存记录为JSON格式

        Args:
            records: 笔记记录列表
            filename: 文件名

        Returns:
            保存的文件路径
        """
        if not filename.endswith('.json'):
            filename += '.json'

        filepath = self.base_path / filename

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)

            print(f"[保存] JSON文件: {filepath}")
            return filepath

        except Exception as e:
            print(f"[错误] 保存JSON失败: {e}")
            return None

    def get_stats(self) -> Dict:
        """
        获取存储统计信息

        Returns:
            统计信息字典
        """
        files = self.list_files()
        total_records = 0
        keywords = set()

        for f in files:
            records = self.read_file(str(f))
            total_records += len(records)
            for r in records:
                if "搜索关键词" in r:
                    keywords.add(r["搜索关键词"])

        return {
            "total_files": len(files),
            "total_records": total_records,
            "keywords": list(keywords),
            "storage_path": str(self.base_path)
        }


if __name__ == "__main__":
    # 测试本地存储
    storage = LocalStorage()

    # 打印统计信息
    stats = storage.get_stats()
    print("=== 存储统计 ===")
    print(f"文件数量: {stats['total_files']}")
    print(f"记录总数: {stats['total_records']}")
    print(f"关键词: {stats['keywords']}")
    print(f"存储路径: {stats['storage_path']}")

    # 列出文件
    files = storage.list_files()
    if files:
        print("\n=== 最近文件 ===")
        for f in files[:5]:
            print(f"  - {f.name}")
