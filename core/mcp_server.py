# mcp_server.py
from mcp.server.fastmcp import FastMCP
from core.agent_tools import get_project_tree, get_function_signature, search_code

# 创建一个 MCP 服务器实例
mcp = FastMCP("AITrader_Tools")

# 使用装饰器将我们的函数注册为大模型可用的工具
@mcp.tool()
def tool_get_project_tree(max_depth: int = 3) -> str:
    """获取项目的核心文件树结构，已自动屏蔽 venv 等无关目录。"""
    return get_project_tree(max_depth)

@mcp.tool()
def tool_get_function_signature(file_path: str, target_name: str) -> str:
    """获取指定 Python 文件中特定函数或类的完整代码块。"""
    return get_function_signature(file_path, target_name)

@mcp.tool()
def tool_search_code(keyword: str) -> str:
    """在项目源码中安全地搜索指定的关键词，并返回带有上下文的代码片段。"""
    return search_code(keyword)

if __name__ == "__main__":
    # 以 stdio (标准输入输出) 模式运行，这是供本地客户端调用的标准模式
    mcp.run()