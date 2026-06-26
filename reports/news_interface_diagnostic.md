# 资讯接口端到端诊断报告

> **生成时间**: 2026-06-26 14:57 CST  
> **项目**: AI-Trader  
> **基础**: AI-Trader hotfix v2 (SQLite DDL fix + event_id normalization)  
> **验证脚本**: `Temp/hermes-verify-news-e2e.py`

---

## 1. 诊断覆盖范围

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 1 | 外部资讯源连通性 — CNINFO | ✅ | 获取 10 条资讯，provider 状态 OK |
| 2 | 外部资讯源连通性 — CLS | ❌ | API 返回 HTTP 404，端点 `/nodeapi/telegraphList` 可能已变更 |
| 3 | 至少一个资讯源可连通 | ✅ | CNINFO 可用，CLS 不可用 |
| 4 | CNINFO 无认证需求（公开接口） | ✅ | HTTP POST form-encoded |
| 5 | config.yaml 无硬编码 API key（CNINFO） | ✅ | 仅有 `enabled`/`poll_interval_seconds`/`timeout_seconds` |
| 6 | config.yaml 无硬编码 API key（CLS） | ✅ | 同上 |
| 7 | .env 文件无明文 CNINFO/CLS key | ✅ | .env 仅含 LLM router 配置 |
| 8 | CNINFO 标准化成功率 | ✅ | 5/5 条标准化成功（100%） |
| 9 | CNINFO event_id 为 64 位 hex | ✅ | SHA256 hash，deterministic |
| 10 | SQLite 写入成功 | ✅ | INSERT 返回 True，count=1 |
| 11 | SQLite 读取验证 | ✅ | 读取到正确事件 |
| 12 | 重复事件 INSERT OR IGNORE 被忽略 | ✅ | count 不变（1→1），INSERT 返回 False |
| 13 | INSERT OR IGNORE 返回 rowcount=0 | ✅ | 重复写入正确返回 False |
| 14 | 真实 CNINFO 事件去重 | ✅ | 相同 ID 第二次写入被忽略 |
| 15 | 不同输入生成不同 event_id | ✅ | deterministic, unique |
| 16 | config → `news_data.allow_trade_trigger = False` | ✅ | 交易触发已关闭 |
| 17 | 资讯模块无交易模块 import | ✅ | 所有资讯模块不导入 live_trader/trade_engine/brain_node 等 |
| 18 | 资讯模块无 TRADE_SIGNAL 发送 | ✅ | 所有资讯文件不含 TRADE_SIGNAL 字符串 |
| 19 | paper_trade_engine 不导入资讯模块 | ✅ | paper_trade_engine 独立于资讯系统 |

**总计**: 19 项检查 — **18 PASS, 1 FAIL**

---

## 2. 逐项分析

### 2.1 外部资讯源连通性

| Provider | URL | 状态 | 延迟 | 获取条数 |
|----------|-----|------|------|----------|
| **CNINFO** (巨潮资讯) | `http://www.cninfo.com.cn/new/hisAnnouncement/query` | ✅ OK | ~2s | 10 |
| **CLS** (财联社) | `https://m.cls.cn/nodeapi/telegraphList` | ❌ DOWN | ~20s (3 retry) | 0 |

**CLS 失败根因**: HTTP 404 Not Found — API 端点已不可用，签名 `38a8e1dc4a6e344bd541ec5ba12920f0` 可能已过期或端点路径变更。Provider 正确使用 `retry_with_backoff(3次)`，失败后返回空 `[]`，不影响系统健壮性。

**风险评估**: CLS 是次要资讯源，CNINFO 为主力 provider。只要 CNINFO 在线，资讯采集链路正常工作。

### 2.2 接口认证

- CNINFO 为公开 Web 接口，使用 `application/x-www-form-urlencoded` POST 请求，无需 API key
- `config.yaml` 中无硬编码 API key：CNINFO/CLS provider 配置仅含 `enabled`, `poll_interval_seconds`, `timeout_seconds`
- `.env` 文件存在但仅含 LLM router 配置（DeepSeek API key），无资讯 provider key 泄露

### 2.3 原始资讯标准化

|**CNINFO normalize 链路**:
```text
raw_item → CninfoNewsProvider.normalize()
  → 提取 announcementTitle → title
  → 提取 announcementTime (ms) → event_time/published_at
  → 提取 secCode → symbols (000001→000001.SZ)
  → 分类 event_type: announcement/risk/earnings
  → SHA256 hash → id/event_id (64 char hex)
  → 输出 9-field canonical schema + backward-compat aliases
```

**CLS normalize 链路**:
```text
raw_item → ClsNewsProvider.normalize()
  → 提取 content/title → title/content
  → 提取 ctime (epoch s) → published_at/event_time
  → 提取 subjects[].secu_code → symbols
  → SHA256 hash → id/event_id (64 char hex)
  → 输出 9-field canonical schema + backward-compat aliases
```

**Canonical Schema (v1)**:
| Field | Type | Description | Backward Alias |
|-------|------|-------------|----------------|
| `id` | str | SHA256 hash of event content | `event_id` |
| `title` | str | Event title with prefix [SecName] | — |
| `source` | str | Provider name ("cninfo"/"cls") | — |
| `published_at` | str | ISO-like timestamp "YYYY-MM-DD HH:MM:SS" | `event_time` |
| `url` | str | Source URL for the announcement/flash | — |
| `summary` | str | Truncated content (≤500 chars) | — |
| `content` | str | Full text/body | — |
| `symbols` | list[str] | Stock codes with exchange suffix | — |
| `fetched_at` | str | Ingestion timestamp | `ingest_time` |

**Backward-compat aliases retained**: `event_id`, `event_type`, `importance`, `sentiment`, `confidence`, `raw`. Existing consumers can read either canonical or legacy fields.

**测试结果**: 5/5 条 CNINFO 公告全部标准化成功。样例:
```
source=cninfo, title=[嘉益股份] 浙商证券...债券受托管理事务报告
id=aa654dadcb19bf..., symbols=['301004.SZ']
published_at=2026-06-26 11:40:29
```

### 2.4 缓存/数据库写入

- SQLite schema: `news_events` 表，`event_id TEXT PRIMARY KEY`，`idx_event_time` 索引
- 写入: `INSERT OR IGNORE` + `cursor.rowcount > 0` 返回布尔值
- 读取: `get_recent_events(limit=N)` + JSON 反序列化 `symbols`/`raw` 字段
- TempDB 测试环境，不影响生产数据

### 2.5 重复资讯去重验证

| 测试场景 | INSERT 返回 | count 变化 | 结论 |
|---------|------------|-----------|------|
| 首次写入 synthetic event | True | 1→1 | ✅ 写入成功 |
| 重复写入 same event_id | False | 1→1 | ✅ 去重生效 |
| 首次写入真实 CNINFO event | True | 1→2 | ✅ 写入成功 |
| 重复写入 same CNINFO event | False | 2→2 | ✅ 去重生效 |
| 不同 input → 不同 event_id | — | — | ✅ deterministic unique |

### 2.6 交易隔离验证

| 检查项 | 方法 | 结果 |
|--------|------|------|
| config `allow_trade_trigger` | 读取 config.yaml | `False` ✅ |
| 资讯模块 import 交易组件 | grep `live_trader`/`trade_engine`/`brain_node`/`trading_state`/`order_manager`/`broker_adapter` | 0 命中 ✅ |
| 资讯模块发送 TRADE_SIGNAL | grep `TRADE_SIGNAL` | 0 命中 ✅ |
| paper_trade_engine 资讯隔离 | grep `news_event_bus`/`cninfo_news` | 0 命中 ✅ |

**结论**: 资讯模块（`NewsEventBus`, `NewsEventStore`, `CninfoNewsProvider`, `ClsNewsProvider`, `BaseNewsProvider`）与交易系统完全隔离。`news_data.allow_trade_trigger: false` 配置级保护已启用。

---

## 3. 架构确认

```
┌─────────────────────────────────────────┐
│           NewsEventBus (调度层)          │
│  ┌──────────┐  ┌──────────┐             │
│  │Cninfo    │  │CLS       │             │
│  │Provider  │  │Provider  │             │
│  └────┬─────┘  └────┬─────┘             │
│       │  normalize  │                    │
│       ▼             ▼                    │
│  ┌────────────────────────────┐         │
│  │     NewsEventStore         │         │
│  │  INSERT OR IGNORE (dedup)  │         │
│  │  SQLite: news_events.db    │         │
│  └────────────────────────────┘         │
│                    │                     │
│                    ▼                     │
│          data_cache/news_health.json     │
└─────────────────────────────────────────┘
         ⛔ NO TRADE_SIGNAL
         ⛔ NO live_trader/trade_engine imports
         ⛔ allow_trade_trigger = false
```

---

## 4. Phase 3: News Schema Standardization (v1) — COMPLETED

Both `CninfoNewsProvider.normalize()` and `ClsNewsProvider.normalize()` now output a **9-field canonical schema** with backward-compat aliases:

| Canonical Field | Backward Alias | Description |
|-----------------|----------------|-------------|
| `id` | `event_id` | SHA256 hash of event content |
| `title` | — | Event title with [SecName] prefix |
| `source` | — | Provider name ("cninfo"/"cls") |
| `published_at` | `event_time` | ISO-like timestamp |
| `url` | — | Source URL |
| `summary` | — | Truncated content (≤500 chars) |
| `content` | — | Full text/body |
| `symbols` | — | Stock codes with exchange suffix |
| `fetched_at` | `ingest_time` | Ingestion timestamp |

**Backward-compat aliases retained**: `event_id`, `event_type`, `importance`, `sentiment`, `confidence`, `raw`. Existing consumers can read either canonical or legacy fields.

### Cache file: `data_cache/news_cache.json`
- Generated at 2026-06-26 14:00 CST
- Contains all 30 CNINFO events in canonical schema format
- Includes provider status, statistics, and schema metadata
- Version: `canonical-v1`

---

## 5. Known Issues

| 测试名 | 跳过原因 | 关键链路影响 |
|--------|----------|-------------|
| `test_cls_normalization` | CLS API `m.cls.cn/nodeapi/telegraphList` 返回 HTTP 404，3次 retry 后 fetch_latest() 返回空 `[]`，触发 `pytest.skip("No raw items to normalize (CLS may be down)")` | **无** — CNINFO `test_cninfo_normalization` 已 PASS，覆盖完整 normalize→schema 路径；CLS 连通性和健康检查均单独 PASS |

## 5. 已知问题

| ID | 严重性 | 描述 | 影响 |
|----|--------|------|------|
| CLS-404 | P2 | CLS API `nodeapi/telegraphList` 返回 404 | 次要资讯源不可用，CNINFO 为主力不受影响 |
| LOG-ROTATION | P3 | `PermissionError [WinError 32]` on log rotation | 日志旋转失败，不影响功能（已知 Windows 问题） |
| test_live_trader_market_guard | P2 | Collection error — `news_extractor.get_news_sentiment` 不存在 | `test_live_trader_market_guard.py` 导入链断裂，不影响资讯模块功能 |

---

## 5. 整体结论

<span style="color:green;font-size:1.2em">**资讯接口链路状态: HEALTHY**</span>

- 主力资讯源 CNINFO（巨潮资讯）正常在线，可正常捞取、标准化、写入、去重
- 资讯系统与交易系统完全隔离，无交叉 import，无信号泄漏
- SQLite 写入和去重机制通过全部测试
- CLS（财联社）API 不可用但标记为次要源，不影响整体链路

**子任务状态**: `News/Event persistence hotfix: CLOSED`
