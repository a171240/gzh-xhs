#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据反哺引擎 - Data Feedback Engine

功能：
1. 读取公众号数据表，分析S级/A级内容
2. 提取标题公式、关键词、开头结构
3. 自动更新方法论文件和关键词库

使用方式：
python data_feedback_engine.py --platform wechat --min-grade A
"""

import os
import re
import argparse
from datetime import datetime
from typing import List, Dict, Tuple


class DataFeedbackEngine:
    """数据反哺引擎"""

    def __init__(self, platform: str = "wechat", min_grade: str = "A"):
        self.platform = platform
        self.min_grade = min_grade
        self.base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 文件路径
        self.data_file = os.path.join(
            self.base_path,
            "04-数据与方法论/内容数据统计/公众号数据.md"
        )
        self.title_method_file = os.path.join(
            self.base_path,
            "04-数据与方法论/方法论沉淀/标题方法论.md"
        )
        self.keyword_file = os.path.join(
            self.base_path,
            "03-素材库/关键词库/公众号核心词库.md"
        )

    def read_data_table(self) -> List[Dict]:
        """读取数据表"""
        if not os.path.exists(self.data_file):
            print(f"❌ 数据文件不存在: {self.data_file}")
            return []

        with open(self.data_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取表格数据
        lines = content.split('\n')
        data_list = []

        for line in lines:
            if line.startswith('|') and '---' not in line and '日期' not in line:
                parts = [p.strip() for p in line.split('|')[1:-1]]
                if len(parts) >= 11:  # 至少11个字段
                    data = {
                        '日期': parts[0],
                        '账号': parts[1],
                        '标题': parts[2],
                        '标题公式': parts[3],
                        '核心关键词': parts[4],
                        '阅读量': parts[5],
                        '在看': parts[6],
                        '转发': parts[7],
                        '评论': parts[8],
                        '完读率': parts[9],
                        '评级': parts[10],
                    }
                    data_list.append(data)

        return data_list

    def filter_high_grade_data(self, data_list: List[Dict]) -> List[Dict]:
        """筛选高评级数据"""
        grade_order = {'S': 4, 'A': 3, 'B': 2, 'C': 1}
        min_grade_value = grade_order.get(self.min_grade, 3)

        filtered = []
        for data in data_list:
            grade = data.get('评级', '').strip()
            if grade and grade_order.get(grade, 0) >= min_grade_value:
                filtered.append(data)

        return filtered

    def analyze_title_patterns(self, data_list: List[Dict]) -> Dict[str, int]:
        """分析标题公式使用频率"""
        pattern_count = {}

        for data in data_list:
            formula = data.get('标题公式', '').strip()
            if formula:
                pattern_count[formula] = pattern_count.get(formula, 0) + 1

        # 按频率排序
        sorted_patterns = sorted(
            pattern_count.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return dict(sorted_patterns)

    def analyze_keywords(self, data_list: List[Dict]) -> Dict[str, Dict]:
        """分析关键词使用情况"""
        keyword_stats = {}

        for data in data_list:
            keywords = data.get('核心关键词', '').strip()
            if not keywords:
                continue

            # 分割多个关键词
            for keyword in keywords.split('/'):
                keyword = keyword.strip()
                if not keyword:
                    continue

                if keyword not in keyword_stats:
                    keyword_stats[keyword] = {
                        '使用次数': 0,
                        '总阅读量': 0,
                        '总在看': 0,
                        '账号分布': set()
                    }

                keyword_stats[keyword]['使用次数'] += 1
                keyword_stats[keyword]['账号分布'].add(data.get('账号', ''))

                # 累计数据
                try:
                    read_count = int(data.get('阅读量', '0').replace(',', ''))
                    like_count = int(data.get('在看', '0').replace(',', ''))
                    keyword_stats[keyword]['总阅读量'] += read_count
                    keyword_stats[keyword]['总在看'] += like_count
                except:
                    pass

        # 计算平均值
        for keyword, stats in keyword_stats.items():
            if stats['使用次数'] > 0:
                stats['平均阅读量'] = stats['总阅读量'] // stats['使用次数']
                stats['平均在看'] = stats['总在看'] // stats['使用次数']
                stats['账号分布'] = '/'.join(sorted(stats['账号分布']))

        return keyword_stats

    def update_title_method(self, patterns: Dict[str, int]) -> bool:
        """更新标题方法论"""
        if not patterns:
            print("⚠️  没有可更新的标题公式")
            return False

        # 生成更新内容
        update_content = f"\n\n## 已验证的高效标题模式（数据反哺更新）\n\n"
        update_content += f"> 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        update_content += "| 标题公式 | 使用次数 | 效果评级 |\n"
        update_content += "|---------|---------|----------|\n"

        for formula, count in patterns.items():
            update_content += f"| {formula} | {count} | ≥{self.min_grade}级 |\n"

        # 追加到文件
        try:
            with open(self.title_method_file, 'a', encoding='utf-8') as f:
                f.write(update_content)
            print(f"✅ 已更新标题方法论: {self.title_method_file}")
            return True
        except Exception as e:
            print(f"❌ 更新标题方法论失败: {e}")
            return False

    def update_keyword_library(self, keyword_stats: Dict[str, Dict]) -> bool:
        """更新关键词库"""
        if not keyword_stats:
            print("⚠️  没有可更新的关键词")
            return False

        if not os.path.exists(self.keyword_file):
            print(f"❌ 关键词库文件不存在: {self.keyword_file}")
            return False

        # 读取现有关键词库
        with open(self.keyword_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 更新关键词使用次数和转化率
        lines = content.split('\n')
        updated_lines = []
        updated_keywords = set()

        for line in lines:
            if line.startswith('|') and '---' not in line and '核心词' not in line:
                parts = [p.strip() for p in line.split('|')[1:-1]]
                if len(parts) >= 7:
                    keyword = parts[0]

                    # 如果关键词在统计中，更新数据
                    if keyword in keyword_stats:
                        stats = keyword_stats[keyword]
                        parts[4] = str(stats['使用次数'])  # 使用次数
                        parts[5] = f"{stats['平均在看']}在看"  # 转化率
                        parts[6] = datetime.now().strftime('%Y-%m-%d')  # 最后更新
                        updated_keywords.add(keyword)

                    updated_line = '| ' + ' | '.join(parts) + ' |'
                    updated_lines.append(updated_line)
                else:
                    updated_lines.append(line)
            else:
                updated_lines.append(line)

        # 写回文件
        try:
            with open(self.keyword_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(updated_lines))
            print(f"✅ 已更新关键词库: {self.keyword_file}")
            print(f"   更新了 {len(updated_keywords)} 个关键词")
            return True
        except Exception as e:
            print(f"❌ 更新关键词库失败: {e}")
            return False

    def run(self):
        """执行数据反哺"""
        print(f"\n{'='*60}")
        print(f"数据反哺引擎启动")
        print(f"平台: {self.platform}")
        print(f"最低评级: {self.min_grade}")
        print(f"{'='*60}\n")

        # 1. 读取数据
        print("📊 读取数据表...")
        data_list = self.read_data_table()
        print(f"   共读取 {len(data_list)} 条数据")

        if not data_list:
            print("❌ 没有数据可分析")
            return

        # 2. 筛选高评级数据
        print(f"\n🔍 筛选 ≥{self.min_grade}级 数据...")
        high_grade_data = self.filter_high_grade_data(data_list)
        print(f"   筛选出 {len(high_grade_data)} 条高评级数据")

        if not high_grade_data:
            print(f"⚠️  没有 ≥{self.min_grade}级 数据")
            return

        # 3. 分析标题公式
        print("\n📈 分析标题公式...")
        patterns = self.analyze_title_patterns(high_grade_data)
        print(f"   发现 {len(patterns)} 种标题公式")
        for formula, count in list(patterns.items())[:5]:
            print(f"   - {formula}: {count}次")

        # 4. 分析关键词
        print("\n🔑 分析关键词...")
        keyword_stats = self.analyze_keywords(high_grade_data)
        print(f"   发现 {len(keyword_stats)} 个关键词")

        # 5. 更新方法论
        print("\n💾 更新方法论...")
        self.update_title_method(patterns)

        # 6. 更新关键词库
        print("\n💾 更新关键词库...")
        self.update_keyword_library(keyword_stats)

        print(f"\n{'='*60}")
        print("✅ 数据反哺完成")
        print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description='数据反哺引擎')
    parser.add_argument(
        '--platform',
        type=str,
        default='wechat',
        help='平台名称 (默认: wechat)'
    )
    parser.add_argument(
        '--min-grade',
        type=str,
        default='A',
        choices=['S', 'A', 'B', 'C'],
        help='最低评级 (默认: A)'
    )

    args = parser.parse_args()

    engine = DataFeedbackEngine(
        platform=args.platform,
        min_grade=args.min_grade
    )
    engine.run()


if __name__ == '__main__':
    main()
