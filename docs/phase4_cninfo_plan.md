# Phase 4 实施方案: CNINFO Provider v2 优化升级

## 1. 修改范围

**仅修改**: `feeds/cninfo_news_provider.py` (重写核心逻辑, 保持接口兼容)
**不改动**: `feeds/news_event_bus.py`, `config/config.yaml`, 其他任何文件

## 2. 升级清单 (7项)

### P0: 分页遍历
- 新增 `max_pages` 构造参数 (默认5)
- 新增 `recent_hours` 构造参数 (默认24)
- `fetch_latest()` 读取 `totalpages`/`hasMore` 实现翻页
- 计算 `actual_max = min(self.max_pages, totalpages)`
- limit参数裁剪最终结果

### P1: 多栏目轮询
- 从固定 `column: "szse"` 改为 `["sse", "szse"]` 轮询
- 每栏目独立翻页
- 合并去重 (按title+secCode去重)

### P1: 时间窗口过滤
- 新增 `_build_date_window()`: `(now - recent_hours) → now` 
- seDate参数: `"2026-06-26~2026-06-27"` 格式
- 与现有SSE/SZSE Provider模式一致

### P2: 速率控制
- 新增 `_rate_limit_wait()`: 随机延时0.5-2.0s
- 复用UA轮换模式 (与SSE/SZSE一致)

### P2: 增强事件分类
- 利用 `classifiedAnnouncements` 或公告标题关键词
- 复用 `_infer_event_type()` 模式 (与SSE/SZSE一致)
- 类型: earnings/risk/corporate_action/governance/announcement

### P2: 强化错误处理
- `_fetch_page()` 响应结构验证
- JSON解析异常捕获
- 空数据优雅降级

### 兼容性保持
- `normalize()` 输出 v1 canonical schema 不变
- `BaseNewsProvider` 接口不变
- `_mark_success()` / `_mark_error()` 状态追踪不变
- `health_check()` 继承基类

## 3. 类签名变更

```python
# 旧
class CninfoNewsProvider(BaseNewsProvider):
    def __init__(self):
        ...

# 新
class CninfoNewsProvider(BaseNewsProvider):
    def __init__(self, max_pages: int = 5, recent_hours: int = 24):
        ...
```

## 4. NewsEventBus适配

`feeds/news_event_bus.py` 当前行:
```python
if provider_cfg.get("cninfo", {}).get("enabled", False):
    self.register_provider("cninfo", CninfoNewsProvider())
```

需改为:
```python
if provider_cfg.get("cninfo", {}).get("enabled", False):
    cninfo_cfg = provider_cfg["cninfo"]
    self.register_provider("cninfo", CninfoNewsProvider(
        max_pages=cninfo_cfg.get("max_pages", 5),
        recent_hours=cninfo_cfg.get("recent_hours", 24),
    ))
```

## 5. Config更新

```yaml
cninfo:
    enabled: true
    poll_interval_seconds: 300
    timeout_seconds: 8
    max_pages: 5                  # 新增
    recent_hours: 24              # 新增
```

## 6. 验收标准

| # | 检查项 | 方法 |
|---|--------|------|
| 1 | py_compile cninfo_news_provider.py | py_compile |
| 2 | 分页遍历: 2页数据正确合并 | 手动测试 |
| 3 | 双栏目: sse+szse均返回数据 | 字段验证 |
| 4 | 时间窗口: seDate参数正确 | 接口实测 |
| 5 | 速率控制: _rate_limit_wait()存在 | 代码检查 |
| 6 | normalize() schema不变 | schema校验 |
| 7 | NewsEventBus初始化兼容 | import测试 |
| 8 | event_type分类增强 | 关键字匹配测试 |
| 9 | 错误处理不崩溃 | 异常边界测试 |
| 10 | 不修改其他文件 | security grep |
