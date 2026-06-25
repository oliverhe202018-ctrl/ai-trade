import os

# 1. Update cninfo_news_provider.py
content1 = open('feeds/cninfo_news_provider.py', encoding='utf-8').read()
content1 = content1.replace('@retry_with_backoff(retries=3, base_delay=2)', '@retry_with_backoff(retries=3, backoff_in_seconds=(2, 5, 10))')
open('feeds/cninfo_news_provider.py', 'w', encoding='utf-8').write(content1)

# 2. Update cls_news_provider.py
content2 = open('feeds/cls_news_provider.py', encoding='utf-8').read()
content2 = content2.replace('@retry_with_backoff(retries=3, base_delay=2)', '@retry_with_backoff(retries=3, backoff_in_seconds=(2, 5, 10))')
open('feeds/cls_news_provider.py', 'w', encoding='utf-8').write(content2)

