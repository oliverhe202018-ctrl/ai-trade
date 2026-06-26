# Phase 5 审计文档: 轻量化 RSS/JSON 资讯源适配器

## 1. 资讯源调研

### 1.1 已验证可用源
| 来源 | 类型 | 状态 | 协议 | 覆盖 |
|------|------|------|------|------|
| Sina Finance Roll API | JSON API | OK | HTTPS GET | 财经头条/美股/A股 |
| feedparser (Python库) | RSS解析器 | 已安装 | RSS/Atom | 通用XML feed |

### 1.2 已验证不可用源
| 来源 | 原因 |
|------|------|
| 东方财富 RSS | HTTP 404 |
| 上海证券报 RSS | 零entries |
| 新浪 RSS feed | 零entries |
| 财联社 API | HTTP 405 |
| 雪球 Hot | WAF拦截 |

### 1.3 Sina Finance Roll API 详情
- **端点**: `https://feed.mix.sina.com.cn/api/roll/get`
- **参数**: `pageid=153, lid=2509` (财经), `lid=2512` (A股) 
- **格式**: JSON `{result: {data: [{title, ctime, url, keywords, intro}]}}`
- **ctime**: Unix timestamp (秒)
- **速率**: 无明确限制, 建议1-2s延时

## 2. 架构设计

### 2.1 通用 RSS 适配器 (`feeds/rss_news_provider.py`)
单一类 `RssNewsProvider` 支持:
- **Mode A — RSS/Atom feed**: 通过 `feedparser` 解析标准 RSS/Atom XML
- **Mode B — JSON API**: 通过 HTTP GET + JSON解析 (如 Sina Finance)
- 构造函数接受 `feeds: List[Dict]` 配置列表

### 2.2 数据源权重降级策略
| 优先级 | 来源 | 降级触发 | 降级操作 |
|--------|------|----------|----------|
| Tier 1 | CNINFO/SSE/SZSE (交易所官方) | DOWN | → 依赖 EastMoney + Sina |
| Tier 2 | EastMoney + Sina RSS | DOWN | → 仅剩交易所公告 |
| Tier 3 | RSS (其他) | DOWN | → 不自动降级, 仅标记 |

### 2.3 配置结构
```yaml
rss:
  enabled: true
  feeds:
    - name: "sina_finance"
      type: "json_api"
      url: "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=20&page=1"
      timeout_seconds: 10
      weight: 1.0            # 权重 (1.0=全量, 0.5=降权)
    - name: "sina_astock"
      type: "json_api"
      url: "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2512&k=&num=20&page=1"
      timeout_seconds: 10
      weight: 0.8
```

## 3. 实施计划

### 新建文件: `feeds/rss_news_provider.py` (~200行)
- `class RssNewsProvider(BaseNewsProvider)`
- `fetch_latest()` → 遍历配置feeds, 按type调用对应解析器
- `normalize()` → 标准 v1 canonical schema
- `_fetch_json_api()` → JSON API 解析
- `_fetch_rss_feed()` → feedparser RSS 解析
- `_infer_event_type()` → 标题关键词推断
- `health_check()` → 健康状态

### 集成: `feeds/news_event_bus.py` + `config/config.yaml`

## 4. 验收标准 (轻量, 7项)
| # | 检查项 |
|---|--------|
| 1 | py_compile |
| 2 | Sina Finance fetch 返回非空 |
| 3 | normalize 输出 v1 schema |
| 4 | event_type 推断 |
| 5 | 降级策略: 单源失败不影响其他 |
| 6 | Config 注册 |
| 7 | Trade isolation |
