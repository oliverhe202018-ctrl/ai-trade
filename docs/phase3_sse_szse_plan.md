# Phase 3 实施方案: SSE + SZSE 公告Provider开发

## 1. 实现目标

新增两个公告Provider, 分别对接上交所(SSE)和深交所(SZSE)官方公告接口, 实现公告全量抓取→筛选→标准化→入库。

## 2. 新增文件清单

| 文件 | 用途 |
|------|------|
| `feeds/sse_news_provider.py` | SSE上交所公告Provider (~250行) |
| `feeds/szse_news_provider.py` | SZSE深交所公告Provider (~220行) |

## 3. 修改文件清单

| 文件 | 修改范围 |
|------|----------|
| `feeds/news_event_bus.py` | 行3-4: import新providers; 行35-47: 注册sse/szse |
| `config/config.yaml` | `news_data.providers` 新增sse和szse配置段 |

## 4. SSE Provider 设计 (`feeds/sse_news_provider.py`)

### 类: `SseNewsProvider(BaseNewsProvider)`

**端点**: `http://query.sse.com.cn/security/stock/queryCompanyBulletin.do`
**方法**: GET + JSONP解析
**分页**: `pageHelp.total/pageCount` 驱动, 默认page_size=30, max_pages=5

**fetch_latest(limit=50)**:
1. 计算日期窗口: 过去24小时
2. 请求第1页, 解析JSONP → 获得total
3. 如果total > page_size且page < max_pages, 翻页
4. 每页延时 `random.uniform(0.5, 2.0)` 秒
5. 返回原始item列表 (limit裁剪)

**normalize(raw_item)**:
- title: `raw_item["TITLE"]`
- source: `"sse"`
- published_at: `raw_item["SSEDATE"]` + `raw_item.get("SSETimeStr", "00:00:00")` → `YYYY-MM-DD HH:MM:SS`
- url: `http://www.sse.com.cn{raw_item["URL"]}`
- symbols: `[f"sh{raw_item['SECURITY_CODE']}"]` (沪市6/9开头)
- summary: title[:500]
- content: title (公告内容在PDF, 不抓取)
- id: `sha256(f"sse{published_at}{title}{url}")`
- event_type: 按BULLETIN_TYPE/BULLETIN_HEADING推断

**事件类型推断**:
| BULLETIN_TYPE | event_type |
|---------------|------------|
| 年报/半年报/季报/业绩预告 | earnings |
| 风险提示/退市风险/立案调查 | risk |
| 减持/增持/回购 | corporate_action |
| 股东大会/董事会决议 | governance |
| 其他 | announcement |

**请求头**:
```python
{
    "User-Agent": "Mozilla/5.0 ... Chrome/120 ...",
    "Referer": "http://www.sse.com.cn/disclosure/listedinfo/announcement/",
    "Accept": "*/*"
}
```

**风控**: 
- 基础延时: `random.uniform(0.5, 2.0)` 秒/请求
- max_pages=5 (最多5页, 约150条)
- 单次fetch最多2.5s延时

## 5. SZSE Provider 设计 (`feeds/szse_news_provider.py`)

### 类: `SzseNewsProvider(BaseNewsProvider)`

**端点**: `http://www.szse.cn/api/disc/announcement/annList`
**方法**: POST JSON
**分页**: `announceCount` 驱动, `ceil(announceCount / page_size)`
**page_size=30, max_pages=5**

**fetch_latest(limit=50)**:
1. 计算日期窗口: 过去24小时
2. POST请求第1页 → 获得announceCount
3. 如果announceCount > page_size且page < max_pages, 翻页
4. 每页延时 `random.uniform(0.5, 2.0)` 秒
5. 返回原始item

**normalize(raw_item)**:
- title: `raw_item["title"]`
- source: `"szse"`
- published_at: `raw_item["publishTime"]` (格式: `YYYY-MM-DD HH:MM:SS`)
- url: 详情页 `http://www.szse.cn/disclosure/listed/fixed/index.html` 或PDF URL `http://disc.static.szse.cn/download{attachPath}`
- symbols: `[f"sz{code}" for code in raw_item["secCode"]]` (深市0/2/3开头)
- summary: title[:500]
- content: title
- id: `sha256(f"szse{published_at}{title}{attachPath}")`

**请求头**:
```python
{
    "User-Agent": "Mozilla/5.0 ... Chrome/120 ...",
    "Referer": "http://www.szse.cn/disclosure/listed/fixed/index.html",
    "Content-Type": "application/json",
    "Origin": "http://www.szse.cn"
}
```

## 6. 集成变更

### config.yaml 新增:
```yaml
news_data:
  providers:
    sse:
      enabled: true
      poll_interval_seconds: 600    # 10分钟 (公告量大)
      timeout_seconds: 15
      max_pages: 5                  # 最大翻页数
      recent_hours: 24              # 时间窗口
    szse:
      enabled: true
      poll_interval_seconds: 600
      timeout_seconds: 15
      max_pages: 5
      recent_hours: 24
```

### news_event_bus.py 注册:
```python
from feeds.sse_news_provider import SseNewsProvider
from feeds.szse_news_provider import SzseNewsProvider

# 在initialize_from_config()中:
if provider_cfg.get("sse", {}).get("enabled", False):
    sse_cfg = provider_cfg["sse"]
    self.register_provider("sse", SseNewsProvider(
        max_pages=sse_cfg.get("max_pages", 5),
        recent_hours=sse_cfg.get("recent_hours", 24)
    ))
if provider_cfg.get("szse", {}).get("enabled", False):
    szse_cfg = provider_cfg["szse"]
    self.register_provider("szse", SzseNewsProvider(
        max_pages=szse_cfg.get("max_pages", 5),
        recent_hours=szse_cfg.get("recent_hours", 24)
    ))
```

## 7. 验收标准

| # | 检查项 | 方法 |
|---|--------|------|
| 1 | SseNewsProvider.fetch_latest() 返回非空列表 | py_compile + 导入测试 |
| 2 | SzseNewsProvider.fetch_latest() 返回非空列表 | py_compile + 导入测试 |
| 3 | normalize() 输出符合v1 canonical schema (9字段) | schema校验 |
| 4 | symbols正确绑定 (SSE→sh前缀, SZSE→sz前缀) | 字段正则验证 |
| 5 | event_type推断逻辑正确 | 分类字段检查 |
| 6 | config.yaml新配置段存在且有效 | yaml parse + key检查 |
| 7 | NewsEventBus正确注册sse/szse | import + register检查 |
| 8 | health_check() 返回正确结构 | 调用health_check() |
| 9 | 禁用情况下不注册 (enabled: false) | 配置切换测试 |
| 10 | 与CNINFO/EastMoney隔离, 不修改现有代码 | security grep |

## 8. 禁止修改文件

- `feeds/cninfo_news_provider.py`
- `feeds/cls_news_provider.py`
- `feeds/eastmoney_news_provider.py`
- `feeds/base_news_provider.py`
- `feeds/news_event_store.py`
- `core/` 任何文件
- `live_trader.py`, `brain_node.py`, `broker_adapter.py`, `trading_state.py`

## 9. 时间估算

| 步骤 | 预计时间 |
|------|----------|
| SSE Provider实现 | 15 min |
| SZSE Provider实现 | 15 min |
| 集成 + config | 5 min |
| ad-hoc验证脚本 | 10 min |
| 覆盖率分析 | 5 min |
| 报告 + 提交 | 5 min |
| **合计** | **~55 min** |
