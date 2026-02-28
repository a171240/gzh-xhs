"""
飞书多维表格存储模块
使用 lark-oapi 官方 SDK 实现数据写入

使用前需要：
1. 在飞书开放平台创建应用
2. 配置应用权限（bitable:app, bitable:record）
3. 创建多维表格并获取 app_token 和 table_id
4. 在 .env 文件中配置凭证
"""
import os
from typing import List, Dict, Optional
from datetime import datetime

try:
    import lark_oapi as lark
    from lark_oapi.api.bitable.v1 import *
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    print("[警告] lark-oapi 未安装，飞书存储功能不可用")
    print("请运行: pip install lark-oapi")

from config import STORAGE_CONFIG, FIELD_MAPPING, FEISHU_FIELD_TYPES


class FeishuStorage:
    """飞书多维表格存储类"""

    def __init__(self):
        self.config = STORAGE_CONFIG["feishu"]
        self.client = None

        if LARK_AVAILABLE:
            self._init_client()

    def _init_client(self):
        """初始化飞书客户端"""
        app_id = self.config["app_id"]
        app_secret = self.config["app_secret"]

        if not app_id or not app_secret:
            print("[警告] 飞书凭证未配置，请检查 .env 文件")
            return

        self.client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .build()

        print("[初始化] 飞书客户端已创建")

    def is_available(self) -> bool:
        """检查飞书存储是否可用"""
        return LARK_AVAILABLE and self.client is not None

    def save_records(self, records: List[Dict]) -> bool:
        """
        批量保存记录到飞书多维表格

        Args:
            records: 笔记记录列表

        Returns:
            是否成功
        """
        if not self.is_available():
            print("[错误] 飞书客户端不可用")
            return False

        if not records:
            print("[提示] 没有记录需要保存")
            return True

        # 转换字段名
        formatted_records = []
        for record in records:
            fields = self._format_record(record)
            formatted_records.append({"fields": fields})

        # 批量插入（每次最多500条）
        batch_size = 500
        total = len(formatted_records)
        success_count = 0

        for i in range(0, total, batch_size):
            batch = formatted_records[i:i+batch_size]
            batch_num = i // batch_size + 1
            print(f"[写入] 批次 {batch_num}, 记录数: {len(batch)}")

            if self._batch_create(batch):
                success_count += len(batch)

        print(f"[完成] 成功写入 {success_count}/{total} 条记录")
        return success_count == total

    def _format_record(self, record: Dict) -> Dict:
        """
        格式化单条记录，将英文字段名转换为中文

        Args:
            record: 原始记录

        Returns:
            格式化后的记录
        """
        fields = {}

        for eng_key, cn_key in FIELD_MAPPING.items():
            if eng_key in record:
                value = record[eng_key]

                # 处理数字字段
                if FEISHU_FIELD_TYPES.get(cn_key) == 2:
                    try:
                        value = int(value) if value else 0
                    except (ValueError, TypeError):
                        value = 0

                # 处理超链接字段
                elif FEISHU_FIELD_TYPES.get(cn_key) == 15:
                    if value:
                        value = {"link": value, "text": "查看笔记"}
                    else:
                        continue

                fields[cn_key] = value

        return fields

    def _batch_create(self, records: List[Dict]) -> bool:
        """
        批量创建记录

        Args:
            records: 格式化后的记录列表

        Returns:
            是否成功
        """
        try:
            # 构建请求
            request = BatchCreateAppTableRecordRequest.builder() \
                .app_token(self.config["app_token"]) \
                .table_id(self.config["table_id"]) \
                .request_body(BatchCreateAppTableRecordRequestBody.builder()
                    .records(records)
                    .build()) \
                .build()

            # 发送请求
            response = self.client.bitable.v1.app_table_record.batch_create(request)

            if not response.success():
                print(f"[错误] 飞书API错误: {response.code} - {response.msg}")
                return False

            return True

        except Exception as e:
            print(f"[错误] 写入飞书失败: {e}")
            return False

    def create_table(self, table_name: str = "小红书对标内容") -> Optional[str]:
        """
        创建新的数据表

        Args:
            table_name: 表格名称

        Returns:
            table_id 或 None
        """
        if not self.is_available():
            print("[错误] 飞书客户端不可用")
            return None

        try:
            # 定义表格字段
            fields = []
            for field_name, field_type in FEISHU_FIELD_TYPES.items():
                fields.append({
                    "field_name": field_name,
                    "type": field_type
                })

            # 构建请求
            request = CreateAppTableRequest.builder() \
                .app_token(self.config["app_token"]) \
                .request_body(CreateAppTableRequestBody.builder()
                    .table(ReqTable.builder()
                        .name(table_name)
                        .fields(fields)
                        .build())
                    .build()) \
                .build()

            # 发送请求
            response = self.client.bitable.v1.app_table.create(request)

            if not response.success():
                print(f"[错误] 创建表格失败: {response.code} - {response.msg}")
                return None

            table_id = response.data.table_id
            print(f"[成功] 创建表格成功，table_id: {table_id}")
            return table_id

        except Exception as e:
            print(f"[错误] 创建表格异常: {e}")
            return None

    def list_tables(self) -> List[Dict]:
        """
        列出多维表格中的所有数据表

        Returns:
            数据表列表
        """
        if not self.is_available():
            return []

        try:
            request = ListAppTableRequest.builder() \
                .app_token(self.config["app_token"]) \
                .build()

            response = self.client.bitable.v1.app_table.list(request)

            if not response.success():
                print(f"[错误] 获取表格列表失败: {response.msg}")
                return []

            tables = []
            if response.data and response.data.items:
                for item in response.data.items:
                    tables.append({
                        "table_id": item.table_id,
                        "name": item.name
                    })

            return tables

        except Exception as e:
            print(f"[错误] 获取表格列表异常: {e}")
            return []


if __name__ == "__main__":
    # 测试飞书存储
    storage = FeishuStorage()

    if storage.is_available():
        print("飞书存储可用")

        # 列出表格
        tables = storage.list_tables()
        print(f"找到 {len(tables)} 个表格:")
        for t in tables:
            print(f"  - {t['name']} ({t['table_id']})")
    else:
        print("飞书存储不可用，请检查配置")
