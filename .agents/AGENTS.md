# Windows 本地环境开发铁律 (Windows Environment Directives)

在处理本项目 Windows 端的本地运行脚本、跨目录执行文件或多进程通信时，必须严格强制遵守以下 3 条底层环境规范，绝不允许省略：

## 1. 批处理脚本 (.bat) 编码防崩溃规则

**痛点**：你生成的 .bat 文件通常为 UTF-8 编码，而 Windows CMD 默认使用 GBK（Code Page 936）。遇到中文注释时会产生乱码，直接导致后续的启动命令被截断或失效。

**强制约束**：任何生成的 .bat 或 .cmd 文件，必须在头部强制声明切换至 UTF-8。

**标准模板**：

```bat
@echo off
chcp 65001
:: 你的中文注释...
```

## 2. 路径收束与工作目录重置 (Working Directory)

**痛点**：严禁假设用户的终端当前处于正确的项目根目录，直接执行相对路径脚本会导致无法预知的寻址错误。

**强制约束**：在批处理文件中执行任何 python、streamlit 或其他可执行文件前，必须先通过 `cd /d` 强制重置工作目录到项目根目录（可使用基于 `%~dp0` 的相对寻址，或明确的绝对路径）。

**标准模板**：

```bat
:: 假设脚本位于 scripts/ 目录，强制退回项目根目录
cd /d "%~dp0.."
```

## 3. Python 跨进程/跨层级执行防丢包 (PYTHONPATH Injection)

**痛点**：Streamlit 大屏、MCP Server (如 Hermes) 或通过 subprocess/bat 启动的独立 Daemon 节点，极易丢失项目根目录的上下文，导致 `ModuleNotFoundError: No module named 'core'`。

**强制约束**：在所有作为独立系统入口的 Python 脚本的绝对最顶端（甚至在内部业务 import 之前），必须强行将项目根目录注入 `sys.path`。

**标准模板**：

```python
import sys, os
# 根据当前脚本的层级自动推导根目录，并强行注入
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 此后才允许导入内部模块
import core.xxx
```
