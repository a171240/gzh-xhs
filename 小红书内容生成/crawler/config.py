"""
小红书爬虫配置文件
配置关键词、存储方式、字段映射等
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).parent.parent

# ===========================================
# 爬虫配置
# ===========================================
CRAWLER_CONFIG = {
    # 平台
    "platform": "xhs",

    # 登录方式：qrcode（二维码）/ cookie（Cookie登录）
    "login_type": "qrcode",

    # 搜索关键词列表（可根据需要修改）
    "keywords": [
        "AI选题工具",
        "AI脚本生成",
        "内容中台",
        "小红书运营工具",
        "代运营SOP",
        "AI写作助手",
        "内容日历",
        "脚本模板",
    ],

    # 每个关键词爬取的页数（每页约20条）
    "pages_per_keyword": 5,

    # 是否爬取评论
    "enable_comments": False,

    # 输出格式：csv / json
    "output_format": "csv",

    # MediaCrawler 安装路径（相对于项目根目录）
    "media_crawler_path": str(BASE_DIR / "MediaCrawler"),
}

# ===========================================
# 存储配置
# ===========================================
STORAGE_CONFIG = {
    # 存储模式：feishu / local / both
    "mode": os.getenv("STORAGE_MODE", "local"),

    # 飞书配置
    "feishu": {
        "app_id": os.getenv("FEISHU_APP_ID", ""),
        "app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "app_token": os.getenv("FEISHU_APP_TOKEN", ""),
        "table_id": os.getenv("FEISHU_TABLE_ID", ""),
    },

    # 本地存储配置
    "local": {
        "base_path": str(BASE_DIR / "data" / "benchmark"),
    },
}

# ===========================================
# 字段映射（MediaCrawler输出字段 -> 中文字段名）
# ===========================================
FIELD_MAPPING = {
    "note_id": "笔记ID",
    "title": "标题",
    "desc": "正文",
    "nickname": "作者",
    "liked_count": "点赞数",
    "collected_count": "收藏数",
    "comment_count": "评论数",
    "create_time": "发布时间",
    "keyword": "搜索关键词",
    "crawl_time": "爬取时间",
    "note_url": "笔记链接",
    "xsec_token": "xsec_token",
}

# ===========================================
# 飞书多维表格字段定义
# ===========================================
FEISHU_FIELD_TYPES = {
    "笔记ID": 1,       # 文本
    "标题": 1,         # 文本
    "正文": 1,         # 文本
    "作者": 1,         # 文本
    "点赞数": 2,       # 数字
    "收藏数": 2,       # 数字
    "评论数": 2,       # 数字
    "发布时间": 1,     # 文本（原始时间戳）
    "搜索关键词": 1,   # 文本
    "爬取时间": 1,     # 文本
    "笔记链接": 15,    # 超链接
}

# ===========================================
# 验证配置
# ===========================================
def validate_config():
    """验证配置是否完整"""
    errors = []

    # 检查存储模式
    if STORAGE_CONFIG["mode"] in ["feishu", "both"]:
        feishu = STORAGE_CONFIG["feishu"]
        if not feishu["app_id"]:
            errors.append("飞书 App ID 未配置")
        if not feishu["app_secret"]:
            errors.append("飞书 App Secret 未配置")
        if not feishu["app_token"]:
            errors.append("飞书多维表格 App Token 未配置")
        if not feishu["table_id"]:
            errors.append("飞书多维表格 Table ID 未配置")

    # 检查关键词
    if not CRAWLER_CONFIG["keywords"]:
        errors.append("搜索关键词列表为空")

    return errors


if __name__ == "__main__":
    # 打印当前配置
    print("=== 爬虫配置 ===")
    print(f"关键词数量: {len(CRAWLER_CONFIG['keywords'])}")
    print(f"每关键词页数: {CRAWLER_CONFIG['pages_per_keyword']}")
    print(f"存储模式: {STORAGE_CONFIG['mode']}")

    # 验证配置
    errors = validate_config()
    if errors:
        print("\n=== 配置错误 ===")
        for e in errors:
            print(f"  - {e}")
    else:
        print("\n配置验证通过")
