# LLaMA Server 高并发生优化总结

## 问题背景

 llama-server 当前配置使用 `--parallel 1`，导致无法处理高并发生场景。当两个请求同时发向 `127.0.0.1:8080` 时，后发请求会因锁机制而 waiting，造成请求丢失或超_。

## 解决方案

已实现以下优化：

### 1. 锁机制与请求排队

由于 llama-server 后端是 `--parallel 1`，实现：

- **互排锁机制**：使用 `asyncio.Lock()` 确保同一时间只有一个请求被处理
- **请求排队**：实现 `request_queue` 机制，新请求自动入隊
- **FIFO 处理**：按加入顺序处理请求，保证 fairness

### 2. HTTP 超_平调整

将 HTTP 超_ bình拆为连接超_平和读超_平：

- **连接超_平**：10 秒（防止 DNS/连接建立过久）
- **读超_平**：180-300 秒（根据实际 token 生成速度调整）
- **总超_平**：连接超_平 + 读超_平

### 3. 独立背景进 trình管理

实现 llama-server 作为独立背景服务：

- **服务监控**：使用 `psutil` 监控进 trình状态
- **自动重修复**：服务 crash 时自动重修复
- **健康检查**：每 30 秒检查 `/health` 端点
- **信号处理**：支持 SIGTERM/SIGINT  graceful shutdown

### 4. 请求指标记录

每次请求记录以下指标：

| 指标 | 说明 |
|-----|------|
| `timestamp` | 请求时间 |
| `prompt_length` | prompt 长度（chars） |
| `max_tokens` | 最大生成 token 数 |
| `duration` | 实际this（秒） |
| `retry_attempts` | 重試学期 |
| `success` | 是否成功 |
| `model` | 使用的模型 |
| `error` | 错误信息（失败时） |

### 5. 自动恢复机制

- **进 trình监控**：持续检查 llama-server 是否 alive
- **健康检查**：HTTP `/health` 端点检查
- **自动 restart**：服务 death 或 health check 失败时，delay 10 秒后自动 restart
- **健康检查**：每 30 秒检查一次

## 文件结构

```
ai-trader/
├── llama_manager.py              # 完整版管理工具
├── llama_service_monitor.py      # 监控器主文件
├── llama_service_wrapper.bat     # Windows 包装文件
├── test_llama_manager.py         # 连接测试
├── requirements.txt              # Python 依赖
├── README_LLAMA_MANAGER.md     # 使用文档
└── LLAMA_SERVER_OPTIMIZATION_SUMMARY.md  # 本文件
```

## 核心代码实现

### 服务管理核心类

```python
class LLamaServerMonitor:
    # 状态管理
    state = ServerState.STOPPED
    
    # 服务启动
    async def start_server(self):
        # 使用 subprocess.Popen 启动 llama-server.exe
        # 设置 CREATE_NO_DUPLICATE 避免子遵问题
    
    # 健康检查
    async def health_check(self) -> bool:
        # HTTP GET /health 检查
    
    # 自动 restart
    async def check_and_restart(self):
        # 检查