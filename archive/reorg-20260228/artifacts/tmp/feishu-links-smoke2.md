# 飞书对标链接处理记录

## 2026-02-20T15:02:00+08:00 run=link_20260220155633_a43eee84
- [failed] https://example.invalid/abc
  - 提炼文本字符：0
  - 金句新增：0，近似复核：0
  - 错误：Firecrawl: Firecrawl 错误: DNS resolution failed for hostname "example.invalid". This means the domain name could not be translated to an IP address. Possible causes: (1) The domain name is misspelled (check for typos), (2) The domain does not exist or has expired, (3) The DNS servers are temporarily unavailable, or (4) The domain was recently registered and DNS has not propagated yet. Please verify the URL is correct and the website exists. | Jina: HTTP 400 | Playwright: Playwright 错误: Page.goto: net::ERR_CONNECTION_ABORTED at https://example.invalid/abc
Call log:
  - navigating to "https://example.invalid/abc", waiting until "networkidle"


