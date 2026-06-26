# Phase 4 审计文档: CNINFO (巨潮资讯) Provider 优化升级

## 1. 现有实现问题诊断

### 1.1 抓取不完整 (P0)
**问题**: `fetch_latest()` 仅请求第1页 (page_num=1), `totalAnnouncement`=359,210条但实际只抓取30条。
**影响**: 覆盖率严重不足 (<=0.23%), 大量公告遗漏。
**根因**: 未使用分页字段 `totalpages` (11973页) 和 `hasMore`。

### 1.2 栏目限制 (P1)
**问题**: 硬编码 `column: "szse"` 仅抓取深市公告, 沪市公告完全缺失。
**影响**: 沪市A股公告覆盖为0, 市场覆盖不完整。
**根因**: `_fetch_page()` 中 `column` 参数固定为 `szse`, 未轮询 `sse`/`szse`/`bj` (北交所)。

### 1.3 无时间窗口过滤 (P1)
**问题**: `seDate` 参数为空字符串, 返回全量匹配。
**影响**: 拉取缓存数据而非最新公告, 时间新鲜度不可控。
**API能力**: 已验证 `seDate: "2026-06-20~2026-06-27"` 支持时间区间。

### 1.4 缺少速率控制 (P2)
**问题**: 无请求间延时, 10ms内可连发请求。
**影响**: 高并发风险触发巨潮资讯风控 (IP暂时封禁)。
**对比**: EastMoney/SSE/SZSE Provider均实现了 `_rate_limit_wait()`。

### 1.5 公告分类缺失 (P2)
**API能力**: 响应包含 `classifiedAnnouncements` 和 `categoryList` 字段。
**当前**: 未提取分类信息, 全部归为 `event_type: "announcement"`。
**机会**: 可按公告类型精确分类 (年报/季报/临时公告/权益分派等)。

### 1.6 错误处理粗糙 (P2)
**问题**: `fetch_latest()` 异常时返回 `[]`, 但 `_fetch_page()` 的 `@retry_with_backoff` 未捕获非200状态码外的JSON解析错误。
**影响**: 响应格式变更时静默失败。

### 1.7 重复数据风险 (P2)
**问题**: 无 `hasMore` 检查, 未实现增量抓取 (仅依赖 `NewsEventStore` SQLite dedup)。
**影响**: 每轮polling重复拉取已有公告, 浪费带宽。

## 2. API能力盘点

| 参数 | 类型 | 说明 | 当前使用 |
|------|------|------|----------|
| `pageNum` | int | 页码 | ✅ 固定1 |
| `pageSize` | int | 每页条数 (最大测试50, 默认30) | ✅ 固定30 |
| `column` | str | `sse`/`szse`/`bj` 交易所筛选 | ❌ 固定szse |
| `seDate` | str | 时间区间 `YYYY-MM-DD~YYYY-MM-DD` | ❌ 空 |
| `tabName` | str | 标签页 | ✅ fulltext |
| `hsecName` | str | 板块名筛选 | ❌ 空 |
| `sortName` | str | 排序字段 | ❌ 空 |
| `showTitle` | str | 标题关键词搜索 | ❌ 空 |

| 响应字段 | 说明 | 当前使用 |
|----------|------|----------|
| `totalAnnouncement` | 总公告数 | ❌ |
| `totalpages` | 总页数 | ❌ |
| `hasMore` | 是否有更多 | ❌ |
| `classifiedAnnouncements` | 分类公告 | ❌ |
| `categoryList` | 类别列表 | ❌ |

## 3. 差距矩阵

| 差距 | 等级 | 修复方案 |
|------|------|----------|
| 单页抓取 | P0 | 分页遍历 (totalpages/hasMore驱动) |
| 栏目限制 | P1 | 多column轮询 (sse+szse) |
| 无时间窗口 | P1 | seDate参数动态计算 |
| 无速率控制 | P2 | 随机延时0.5-2.0s |
| 分类缺失 | P2 | 利用classifiedAnnouncements推断event_type |
| 错误处理 | P2 | 增加响应结构验证+JSON解析保护 |
| 增量抓取 | P2 | _last_event_time记录+按时间过滤 |

## 4. 审计结论

**WHY FIX**: CNINFO是项目主资讯源 (primary), 当前仅抓取深市第1页30条, 359,210条公告中覆盖<0.01%, P0级阻塞问题必须立即修复。

**IMPACT**: 修复后CNINFO单provider即可覆盖两市全量公告 (359,210条), 覆盖率从0.23%跃升至10-20%, 进入WEAK-HEALTHY门控区间。

**BOUNDARIES**: CNINFO公告为巨潮资讯聚合 (含新三板/北交所), 需secCode过滤仅保留A股。pageSize上限约50, 翻页受totalpages限制。

**RISKS**: 巨潮资讯API无公开文档, 参数可能变更; 反爬门槛随时间升高; 359,210条全量翻页不可行, 需配合时间窗口限制每次拉取量。

**NEXT STEPS**:
1. 输出实施方案 → 2. 实现优化 → 3. 集成测试 → 4. ad-hoc验证
