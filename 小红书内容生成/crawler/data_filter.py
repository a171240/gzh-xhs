# -*- coding: utf-8 -*-
"""
数据过滤脚本 - 筛选高质量内容
根据时间范围和互动指标过滤爬取的数据
"""

import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any

# 过滤配置
FILTER_CONFIG = {
    # 时间范围（天数）
    "time_range_days": 90,  # 3个月

    # 高热词质量标准
    "high_hot_min_likes": 50,
    "high_hot_min_engagement": 100,  # 点赞+收藏+评论

    # 低热词质量标准（蓝海词门槛低一些）
    "low_hot_min_likes": 50,
    "low_hot_min_engagement": 100,

    # 高热高转化关键词列表
    "high_hot_keywords": [
        "AI写作工具哪个好", "自媒体运营工具推荐", "短视频脚本AI生成",
        "AI文案生成工具", "AI脚本生成", "AI选题工具", "AI写作助手",
        "小红书运营工具", "短视频脚本模板", "内容中台平台对比",
        "副业赚钱方法", "AI副业项目", "自媒体怎么做", "ChatGPT使用教程",
        "爆文标题模板", "选题模板", "爆款标题公式", "小红书涨粉技巧",
        "自媒体写作技巧", "爆款脚本范例"
    ],

    # 低热高转化关键词列表（蓝海词）
    "low_hot_keywords": [
        "内容中台系统方案", "多账号内容管理工具", "MCN内容管理系统",
        "小红书代运营平台", "SaaS内容创作平台", "账号矩阵管理",
        "内容排期工具", "运营流程SOP", "私域内容运营系统", "内容中台系统"
    ]
}


def parse_count(count_str: str) -> int:
    """解析数量字符串，如 '1.2万' -> 12000"""
    if not count_str:
        return 0
    count_str = str(count_str).strip()
    if '万' in count_str:
        return int(float(count_str.replace('万', '')) * 10000)
    try:
        return int(count_str)
    except:
        return 0


def is_within_time_range(timestamp_ms: int, days: int) -> bool:
    """检查时间戳是否在指定天数范围内"""
    if not timestamp_ms:
        return False
    note_time = datetime.fromtimestamp(timestamp_ms / 1000)
    cutoff_time = datetime.now() - timedelta(days=days)
    return note_time >= cutoff_time


def filter_notes(notes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    过滤笔记数据
    返回: {
        "high_quality": [...],  # 高质量内容
        "medium_quality": [...],  # 中等质量
        "all_within_range": [...]  # 时间范围内所有内容
    }
    """
    config = FILTER_CONFIG
    high_quality = []
    medium_quality = []
    all_within_range = []

    for note in notes:
        # 1. 时间过滤
        note_time = note.get("time", 0)
        if not is_within_time_range(note_time, config["time_range_days"]):
            continue

        all_within_range.append(note)

        # 2. 计算互动指标
        likes = parse_count(note.get("liked_count", "0"))
        collects = parse_count(note.get("collected_count", "0"))
        comments = parse_count(note.get("comment_count", "0"))
        engagement = likes + collects + comments

        # 3. 判断关键词类型
        keyword = note.get("source_keyword", "")
        is_low_hot = keyword in config["low_hot_keywords"]

        # 4. 应用质量标准
        if is_low_hot:
            min_likes = config["low_hot_min_likes"]
            min_engagement = config["low_hot_min_engagement"]
        else:
            min_likes = config["high_hot_min_likes"]
            min_engagement = config["high_hot_min_engagement"]

        if likes >= min_likes and engagement >= min_engagement:
            high_quality.append(note)
        elif likes >= min_likes // 2 or engagement >= min_engagement // 2:
            medium_quality.append(note)

    return {
        "high_quality": high_quality,
        "medium_quality": medium_quality,
        "all_within_range": all_within_range
    }


def generate_report(original_count: int, filtered: Dict) -> str:
    """生成过滤报告"""
    report = []
    report.append("=" * 60)
    report.append("[REPORT] 数据过滤报告")
    report.append("=" * 60)
    report.append(f"原始数据量: {original_count} 条")
    report.append(f"时间范围内: {len(filtered['all_within_range'])} 条 (近{FILTER_CONFIG['time_range_days']}天)")
    report.append(f"高质量内容: {len(filtered['high_quality'])} 条")
    report.append(f"中等质量: {len(filtered['medium_quality'])} 条")
    report.append("")

    # 按关键词统计
    keyword_stats = {}
    for note in filtered['all_within_range']:
        kw = note.get("source_keyword", "未知")
        if kw not in keyword_stats:
            keyword_stats[kw] = {"total": 0, "high": 0}
        keyword_stats[kw]["total"] += 1
        if note in filtered['high_quality']:
            keyword_stats[kw]["high"] += 1

    report.append("[*] 各关键词数据统计:")
    report.append("-" * 40)
    for kw, stats in sorted(keyword_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        report.append(f"  {kw}: {stats['total']}条 (高质量: {stats['high']}条)")

    return "\n".join(report)


def main():
    """主函数"""
    # 数据文件路径
    data_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(data_dir, "data", "笔记内容.json")

    # 检查文件是否存在
    if not os.path.exists(input_file):
        # 尝试从MediaCrawler目录读取
        import glob
        media_crawler_files = glob.glob(
            os.path.join(data_dir, "..", "MediaCrawler", "data", "xhs", "json", "search_contents_*.json")
        )
        if media_crawler_files:
            input_file = max(media_crawler_files, key=os.path.getmtime)
        else:
            print("[X] 未找到数据文件，请先运行爬虫")
            return

    print(f"[*] 读取数据文件: {input_file}")

    # 读取数据
    with open(input_file, "r", encoding="utf-8") as f:
        notes = json.load(f)

    print(f"[*] 原始数据: {len(notes)} 条笔记")

    # 过滤数据
    filtered = filter_notes(notes)

    # 生成报告
    report = generate_report(len(notes), filtered)
    print(report)

    # 保存过滤后的数据
    output_dir = os.path.join(data_dir, "data")
    os.makedirs(output_dir, exist_ok=True)

    # 保存高质量数据
    high_quality_file = os.path.join(output_dir, "高质量笔记.json")
    with open(high_quality_file, "w", encoding="utf-8") as f:
        json.dump(filtered["high_quality"], f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 高质量数据已保存: {high_quality_file}")

    # 保存时间范围内所有数据
    all_file = os.path.join(output_dir, "近3个月笔记.json")
    with open(all_file, "w", encoding="utf-8") as f:
        json.dump(filtered["all_within_range"], f, ensure_ascii=False, indent=2)
    print(f"[OK] 时间范围内数据已保存: {all_file}")


if __name__ == "__main__":
    main()
