"""
微信公众号自动发布脚本
版本: v1.0
日期: 2026-01-03

使用前需要:
1. 已认证的公众号
2. 获取AppID和AppSecret
3. 配置IP白名单
"""

import requests
import json
import os
import re
import markdown
from datetime import datetime
from pathlib import Path


class WeChatPublisher:
    """微信公众号发布器"""

    def __init__(self, appid: str, appsecret: str):
        self.appid = appid
        self.appsecret = appsecret
        self.base_url = "https://api.weixin.qq.com/cgi-bin"
        self.access_token = None
        self.token_expires = None

    def get_access_token(self) -> str:
        """获取access_token（有效期2小时）"""
        url = f"{self.base_url}/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.appid,
            "secret": self.appsecret
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()

            if "access_token" in data:
                self.access_token = data["access_token"]
                self.token_expires = datetime.now()
                print(f"✅ 获取access_token成功")
                return self.access_token
            else:
                print(f"❌ 获取access_token失败: {data}")
                return None
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            return None

    def upload_image(self, image_path: str) -> str:
        """上传图片素材（永久素材）"""
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return None

        url = f"{self.base_url}/material/add_material"
        params = {
            "access_token": self.access_token,
            "type": "image"
        }

        try:
            with open(image_path, "rb") as f:
                files = {"media": f}
                resp = requests.post(url, params=params, files=files, timeout=30)
                data = resp.json()

            if "media_id" in data:
                print(f"✅ 上传图片成功: {data['media_id']}")
                return data["media_id"]
            else:
                print(f"❌ 上传图片失败: {data}")
                return None
        except Exception as e:
            print(f"❌ 上传失败: {e}")
            return None

    def upload_content_image(self, image_path: str) -> str:
        """上传图文内容中的图片（返回URL）"""
        if not os.path.exists(image_path):
            print(f"❌ 图片不存在: {image_path}")
            return None

        url = f"{self.base_url}/media/uploadimg"
        params = {"access_token": self.access_token}

        try:
            with open(image_path, "rb") as f:
                files = {"media": f}
                resp = requests.post(url, params=params, files=files, timeout=30)
                data = resp.json()

            if "url" in data:
                print(f"✅ 上传内容图片成功")
                return data["url"]
            else:
                print(f"❌ 上传内容图片失败: {data}")
                return None
        except Exception as e:
            print(f"❌ 上传失败: {e}")
            return None

    def create_draft(self, title: str, content: str, thumb_media_id: str,
                     author: str = "李可", digest: str = None) -> str:
        """新建草稿"""
        url = f"{self.base_url}/draft/add"
        params = {"access_token": self.access_token}

        # 构建文章数据
        article = {
            "title": title,
            "author": author,
            "content": content,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 1,
            "only_fans_can_comment": 0
        }

        if digest:
            article["digest"] = digest[:120]  # 摘要最多120字

        data = {"articles": [article]}

        try:
            resp = requests.post(url, params=params, json=data, timeout=30)
            result = resp.json()

            if "media_id" in result:
                print(f"✅ 创建草稿成功: {result['media_id']}")
                return result["media_id"]
            else:
                print(f"❌ 创建草稿失败: {result}")
                return None
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            return None

    def publish_draft(self, media_id: str) -> str:
        """发布草稿"""
        url = f"{self.base_url}/freepublish/submit"
        params = {"access_token": self.access_token}
        data = {"media_id": media_id}

        try:
            resp = requests.post(url, params=params, json=data, timeout=30)
            result = resp.json()

            if result.get("errcode") == 0:
                publish_id = result.get("publish_id")
                print(f"✅ 发布任务提交成功: {publish_id}")
                return publish_id
            else:
                print(f"❌ 发布失败: {result}")
                return None
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            return None

    def check_publish_status(self, publish_id: str) -> dict:
        """查询发布状态"""
        url = f"{self.base_url}/freepublish/get"
        params = {"access_token": self.access_token}
        data = {"publish_id": publish_id}

        try:
            resp = requests.post(url, params=params, json=data, timeout=10)
            result = resp.json()

            # 发布状态: 0-成功, 1-发布中, 2-原创失败, 3-常规失败, 4-平台审核不通过, 5-成功后用户删除, 6-成功后系统封禁
            status_map = {
                0: "✅ 发布成功",
                1: "⏳ 发布中",
                2: "❌ 原创失败",
                3: "❌ 常规失败",
                4: "❌ 平台审核不通过",
                5: "⚠️ 成功后用户删除",
                6: "⚠️ 成功后系统封禁"
            }

            publish_status = result.get("publish_status", -1)
            print(f"发布状态: {status_map.get(publish_status, '未知')}")

            return result
        except Exception as e:
            print(f"❌ 查询失败: {e}")
            return None


class ArticleParser:
    """文章解析器：解析生成的md文件"""

    @staticmethod
    def parse_md_file(file_path: str) -> dict:
        """解析md文件，提取标题和内容"""
        if not os.path.exists(file_path):
            print(f"❌ 文件不存在: {file_path}")
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        result = {
            "title": None,
            "content": None,
            "frontmatter": {}
        }

        # 解析frontmatter（如果有）
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_text = parts[1].strip()
                content = parts[2].strip()

                # 简单解析frontmatter
                for line in frontmatter_text.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        result["frontmatter"][key.strip()] = value.strip().strip('"')

        # 提取主标题
        title_match = re.search(r'\*\*主标题\*\*[：:]\s*(.+)', content)
        if title_match:
            result["title"] = title_match.group(1).strip()

        # 提取正文（从"## 正文"开始到"# 配图提示词"之前）
        content_match = re.search(r'## 正文\s*\n(.*?)(?=\n# 配图提示词|\Z)', content, re.DOTALL)
        if content_match:
            result["content"] = content_match.group(1).strip()

        return result

    @staticmethod
    def md_to_html(md_content: str) -> str:
        """将Markdown转换为微信支持的HTML"""
        # 使用markdown库转换
        html = markdown.markdown(md_content, extensions=['extra', 'nl2br'])

        # 微信公众号样式优化
        # 1. 段落间距
        html = html.replace("<p>", '<p style="margin-bottom: 1em;">')

        # 2. 加粗样式
        html = html.replace("<strong>", '<strong style="color: #333;">')

        # 3. 列表样式
        html = html.replace("<ul>", '<ul style="padding-left: 1.5em;">')
        html = html.replace("<ol>", '<ol style="padding-left: 1.5em;">')

        # 4. 分割线
        html = html.replace("<hr>", '<hr style="border: none; border-top: 1px solid #eee; margin: 2em 0;">')

        return html


def load_config(config_path: str = None) -> dict:
    """加载配置文件"""
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "config.json")

    if not os.path.exists(config_path):
        print(f"❌ 配置文件不存在: {config_path}")
        return None

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def publish_article(account_name: str, md_file_path: str, cover_image_path: str = None):
    """
    发布文章到指定公众号

    Args:
        account_name: 公众号账号名称（如"IP内容工厂"）
        md_file_path: 生成的md文件路径
        cover_image_path: 封面图片路径（可选，默认使用默认封面）
    """
    print(f"\n{'='*50}")
    print(f"开始发布到: {account_name}")
    print(f"{'='*50}\n")

    # 1. 加载配置
    config = load_config()
    if not config:
        return False

    account_config = config.get("accounts", {}).get(account_name)
    if not account_config:
        print(f"❌ 未找到账号配置: {account_name}")
        return False

    if not account_config.get("enabled", False):
        print(f"❌ 账号未启用: {account_name}")
        return False

    # 2. 解析文章
    parser = ArticleParser()
    article = parser.parse_md_file(md_file_path)
    if not article or not article["title"] or not article["content"]:
        print("❌ 解析文章失败")
        return False

    print(f"📄 文章标题: {article['title']}")

    # 3. 转换为HTML
    html_content = parser.md_to_html(article["content"])

    # 4. 初始化发布器
    publisher = WeChatPublisher(
        appid=account_config["appid"],
        appsecret=account_config["appsecret"]
    )

    # 5. 获取access_token
    if not publisher.get_access_token():
        return False

    # 6. 上传封面图
    if cover_image_path is None:
        # 使用默认封面
        cover_image_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "covers",
            "default_cover.jpg"
        )

    thumb_media_id = publisher.upload_image(cover_image_path)
    if not thumb_media_id:
        print("❌ 上传封面图失败")
        return False

    # 7. 创建草稿
    media_id = publisher.create_draft(
        title=article["title"],
        content=html_content,
        thumb_media_id=thumb_media_id
    )
    if not media_id:
        return False

    # 8. 发布草稿
    publish_id = publisher.publish_draft(media_id)
    if not publish_id:
        return False

    # 9. 查询发布状态（延迟查询）
    import time
    print("\n⏳ 等待发布完成...")
    time.sleep(5)
    status = publisher.check_publish_status(publish_id)

    print(f"\n{'='*50}")
    print(f"✅ 发布流程完成!")
    print(f"请在微信公众平台后台 → 发表记录 中查看")
    print(f"{'='*50}\n")

    return True


# 命令行使用
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("使用方法: python wechat_publisher.py <账号名称> <md文件路径> [封面图路径]")
        print("示例: python wechat_publisher.py IP内容工厂 ./生成内容/2026-01-03/gongchang-xxx.md")
        sys.exit(1)

    account = sys.argv[1]
    md_file = sys.argv[2]
    cover = sys.argv[3] if len(sys.argv) > 3 else None

    success = publish_article(account, md_file, cover)
    sys.exit(0 if success else 1)
