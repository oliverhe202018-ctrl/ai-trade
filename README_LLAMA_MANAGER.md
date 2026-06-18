# LLaMA Server 管理工具

## 功能特性

1. **服务监控与自动重啟** - 监控 llama-server 状态，异常时自动恢复
2. **请求排队机制** - 由于 llama-server `--parallel 1`，实现外部请求排队
3. **HTTP 超_平管理** - 连接超_平 10 秒，读超_平 180-300 秒可配
4. **请求指标记录** - 记录每次请求的 prompt tokens、max tokens、实际 this、Retry 状态
5. **健康检查** - 每 30 秒检查服务健康状态
6. **独立背景进 trình管理** - 不依赖手工 bat 启动

## 文件结构

```
ai-trader/
├── llama_manager.py              # 完整版管理工具（带完整功能）
├── llama_service_monitor.py      # 监控器（背景服务）
├── llama_service_wrapper.bat     # Windows 包装 bat 文件
├── test_llama_manager.py         # 测试连接
├── requirements.txt              # Python 依赖
└── README_LLAMA_MANAGER.md      # 本文件
```

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方式

### 1. 启动服务

```bash
# 直接运行监控器
python llama_service_monitor.py --start

# 或运行完整版管理工具
python llama_manager.py --start
```

### 2. 停止服务

```bash
python llama_service_monitor.py --stop
```

### 3. 查看状态

```bash
python llama_service_monitor.py --status
```

### 4. 添加请求到排队

```python
import llama_service_monitor

monitor = llama_service_monitor.LLamaServerMonitor()

# 添加请求
monitor.add_request(
    prompt="你好，请给我写一段 Python 代码，计算 1 到 100 的累计和。",
    max_tokens=512,
    model="uncensored"
)

# 运行主循环
asyncio.run(monitor.run())
```

### 5. 直接运行管理工具

```bash
python llama_manager.py
```

会示例性发送一个请求。

## 配置说明

### llama_manager.py 配置

```python
config = ServerConfig(
    host="127.0.0.1",           # 服务地址
    port=8080,                   # 服务_port
    max_parallel=4,              # 最大并发生成数（受--parallel 1 限制）
    read_timeout=300,            # 读超_平 180-300 秒
    connection_timeout=30,       # 连接超_平
    health_check_interval=30,    # 健康检查间隔
    restart_delay=10,            # 重启 delay
)
```

### llama_service_monitor.py 配置

```python
self.config = {
    "llama_binary": "H:\\llama-b9616-bin-win-cuda-12.4-x64\\llama-server.exe",
    "model_path": "C:\\Users\\a2515\\ai-trader\\models\\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL.gguf",
    "host": "127.0.0.1",
    "port": 8080,
    "health_check_interval": 30,
    "restart_delay": 10,
    "max_parallel": 2,
}
```

## 指标记录

每次请求会记录：

- `timestamp`: 时间 stamp
- `prompt_length`: prompt 长度
- `max_tokens`: 最大生成 token 数
- `duration`: 实际 this（秒）
- `retry_attempts`: 重試学期
- `success`: 是否成功
- `model`: 使用的模型
- `error`: 错误信息（如失败）

## 锁机制说明

由于 llama-server 使用 `--parallel 1`，单生
</think>

<tool_call>
<function=terminal>
<parameter=command>
cd C:\Users\a2515\ai-trader && python -c "import llama_service_monitor; print('Import successful')"