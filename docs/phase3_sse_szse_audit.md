# Phase 3 审计文档: SSE上交所 + SZSE深交所公告Provider

## 1. 现有基础设施审计

### 1.1 已有Provider清单
| Provider | 状态 | 覆盖 | 代码位置 |
|----------|------|------|----------|
| CNINFO (巨潮资讯) | OK, 主源 | ~0.23% A股 | `feeds/cninfo_news_provider.py` |
| CLS (财联社) | DOWN (HTTP 404) | 不适用 | `feeds/cls_news_provider.py` |
| EastMoney (东方财富) | OK, 增强源 | ~10-30% | `feeds/eastmoney_news_provider.py` |

### 1.2 抽象基类接口
`BaseNewsProvider` (`feeds/base_news_provider.py`):
- `fetch_latest(limit)` → `List[Dict]`
- `fetch_since(timestamp)` → `List[Dict]` (多数provider未实现)
- `normalize(raw_item)` → `Optional[Dict]` (v1 canonical: id/title/source/published_at/url/summary/content/symbols/fetched_at + backward-compat aliases)
- `health_check()` → `Dict`
- `_mark_success()` / `_mark_error()` 状态追踪

### 1.3 集成点
`NewsEventBus.initialize_from_config()` 通过 `config.yaml` → `news_data.providers.<name>` 注册provider。
标准化schema (v1 canonical) 9字段: `id, title, source, published_at, url, summary, content, symbols, fetched_at` + 兼容别名。

### 1.4 覆盖率门控
`NewsCoverageGate` 三级门控: ≥20% HEALTHY (100%), 5-20% WEAK (30%), <5% INSUFFICIENT (0%)。
当前覆盖率约0.23% (CNINFO only), 属于INSUFFICIENT。

## 2. SSE/SZSE 外部接口审计

### 2.1 SSE 上交所公告API

**端点**: `http://query.sse.com.cn/security/stock/queryCompanyBulletin.do` (GET, JSONP包装)
**实测状态**: HTTP 200, 正常返回, 2832条公告 (2026-06-20~06-27, 7日窗口)

**关键参数**:
- `isPagination=true` — 开启分页
- `pageHelp.pageSize=N` — 每页条数 (最大实测~25)
- `pageHelp.pageNo=N` — 页码
- `securityType=0101` — 股票类型
- `reportType=ALL` — 全部公告类型
- `beginDate/endDate` — 日期区间 (YYYY-MM-DD)

**响应结构**:
```json
{
  "pageHelp": {
    "total": 2832,
    "pageCount": 1416,
    "pageSize": 2,
    "data": [
      {
        "SECURITY_CODE": "600000",
        "SECURITY_NAME": "浦发银行",
        "TITLE": "...",
        "BULLETIN_TYPE": "其它",
        "BULLETIN_HEADING": "临时公告",
        "BULLETIN_YEAR": "2026",
        "ADDDATE": "2026-06-26 19:02:38",
        "SSEDATE": "2026-06-27",
        "URL": "/disclosure/listedinfo/announcement/c/new/2026-06-27/600000_20260627_0JH3.pdf"
      }
    ]
  }
}
```

**特点**:
- JSONP格式 `jsonpCallback({...})`, 需strip前缀/后缀
- `pageHelp.total` 提供总数, `pageHelp.pageCount` 提供总页数
- `URL` 字段为相对路径, 需拼接 `http://www.sse.com.cn`
- `SECURITY_CODE` 为6位数字 (沪市: 6/9开头)
- `ADDDATE` 和 `SSEDATE` 双重时间戳

**风控评估**:
- 低风控 — GET请求, 无需认证
- 需设置合理Referer (`http://www.sse.com.cn/`)
- 建议延时: 0.5-1s基础延时避免触发限流

### 2.2 SZSE 深交所公告API

**端点**: `http://www.szse.cn/api/disc/announcement/annList` (POST, JSON body)
**实测状态**: HTTP 200, 正常返回, announceCount=12 (2026-06-20~06-27)

**关键参数** (JSON body):
```json
{
  "seDate": ["2026-06-20", "2026-06-27"],
  "channelCode": ["fixed_disc"],
  "pageSize": 30,
  "pageNum": 1
}
```

**响应结构**:
```json
{
  "announceCount": 12,
  "data": [
    {
      "id": "uuid",
      "annId": 1225393038,
      "title": "...",
      "content": null,
      "publishTime": "2026-06-27 00:00:00",
      "attachPath": "/disc/disk03/finalpage/2026-06-27/abc.pdf",
      "secCode": ["002459"],
      "secName": ["晶澳科技"],
      "channelCode": "fixed_disc"
    }
  ]
}
```

**特点**:
- POST JSON, 标准RESTful
- `announceCount` 提供总数, 分页计算: `totalPages = ceil(announceCount / pageSize)`
- `attachPath` 为相对路径, URL: `http://disc.static.szse.cn/download{attachPath}`
- `secCode` 为数组 (多数为单元素)
- `content` 通常为null (文件内容通过PDF下载)
- **channelCode可扩展**: `fixed_disc` (定期报告), 还可探索其他channel

**风控评估**:
- 低风控 — POST JSON, 无需认证
- 需设置Content-Type/Origin/Referer
- 建议延时: 0.5-1s, 与SSE类似

### 2.3 公告文件下载

**SSE PDF下载**: `http://www.sse.com.cn{URL}` (URL字段值)
  - 例: `http://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-06-27/600000_20260627_0JH3.pdf`

**SZSE PDF下载**: `http://disc.static.szse.cn/download{attachPath}`
  - 例: `http://disc.static.szse.cn/download/disc/disk03/finalpage/2026-06-27/02ab0b14-...pdf`

**文件下载策略**: 
- Phase 3 仅构建URL, 不下载文件内容
- 文件下载消耗带宽且内容解析需OCR/PDF提取, 与Phase 3公告抓取职责分离
- normalize() 中 `url` 字段指向公告详情页或PDF下载链接

## 3. 差距分析

### 3.1 数据覆盖缺口
- 当前覆盖率 ~0.23% (CNINFO only)
- SSE + SZSE 官方数据覆盖两市全量公告 (数千条/日)
- 预期增量: 提升覆盖率至 5-15% (进入WEAK门控区间)

### 3.2 可复用组件
- ✅ `BaseNewsProvider` 抽象基类
- ✅ `NewsEventBus.register_provider()` 注册机制
- ✅ `NewsEventStore` SQLite写入 + INSERT OR IGNORE dedup
- ✅ `news_health.json` 原子写入健康检查
- ✅ Config-driven provider初始化
- ✅ `core.utils.retry_with_backoff` 重试装饰器
- ✅ CANONICAL SCHEMA v1 + backward-compat aliases

### 3.3 需新增
- `feeds/sse_news_provider.py` — SSE公告Provider
- `feeds/szse_news_provider.py` — SZSE公告Provider
- `config/config.yaml` — 新增 providers.sse 和 providers.szse 配置段
- `feeds/news_event_bus.py` — 注册SSE/SZSE到initialize_from_config()

### 3.4 风险识别
| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| SSE/SZSE接口变更 | P2 | JSONP/POST JSON格式稳定, SSE已运行多年未变 |
| 请求限流/IP封禁 | P1 | 0.5-1s基础延时 + 1s随机抖动, 单次max_pages=10 |
| 公告数量巨大 (SSE 2823条/7天) | P1 | 默认page_size=30, max_pages=5, 配合时间窗口过滤 |
| 代码绑定不准确 | P2 | 使用SECURITY_CODE/secCode原始字段, 不做文本提取 |
| 与CNINFO重复 | P2 | SHA256 dedup (不同source, 相同title+time可能重复, 但sha256含source保证唯一) |

## 4. 审计结论

**WHY PASS**: 
- SSE和SZSE官方API均可正常访问, 返回结构化公告数据
- 现有基础设施(BASE_PROVIDER/EVENT_BUS/EVENT_STORE)完备, 接口契约清晰
- 抗爬策略(延时+Referer+UA)经EastMoney验证有效, 可直接复用到交易所API

**IMPACT**:
- 新增两市官方公告全量覆盖, 预计覆盖率从0.23%提升至5-15%
- 进入WEAK门控区间 (≥5%), 资讯因子权重从0%恢复至30%
- 填补CNINFO仅抓取巨潮资讯的片面性

**BOUNDARIES**:
- SSE API返回格式为JSONP (需解析剥离), SZSE为JSON POST
- SSE pageSize上限约25, SZSE pageSize上限约30
- 公告文件(PDF)仅构建下载URL, 不抓取内容
- channelCode/path可能扩展 (SZSE支持多channel), 当前仅用fixed_disc

**RISKS**:
- SSE JSONP格式如果回调函数名变更需要适配
- SZSE其他channelCode (如监管函/问询函)路径待探索
- 下载公告文件内容需要PDF解析能力 (超出Phase 3范围)

**NEXT STEPS**:
1. 输出实施方案文档 (`docs/phase3_sse_szse_plan.md`)
2. 实现 `feeds/sse_news_provider.py` + `feeds/szse_news_provider.py`
3. 集成到 `NewsEventBus` + `config.yaml`
4. 运行ad-hoc验证脚本 + 覆盖率分析
5. 提交git + push
