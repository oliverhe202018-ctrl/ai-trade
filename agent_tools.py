import os
import ast
from pathlib import Path

# FIX 1: 强制锁定绝对路径！防止 Hermes 在系统根目录下乱跑
SAFE_DIR = Path(r"C:\Users\a2515\ai-trader")
IGNORE_DIRS = {'venv', '.git', '__pycache__', 'logs', '.idea'}
IGNORE_EXTS = {'.csv', '.log', '.exe', '.dll', '.gguf'}

def get_project_tree(max_depth: int = 3) -> str:
    """工具1：获取项目核心文件树"""
    tree_str = []
    
    def walk_dir(current_path, depth):
        if depth > max_depth:
            return
        try:
            for item in current_path.iterdir():
                if item.name in IGNORE_DIRS or item.suffix in IGNORE_EXTS:
                    continue
                indent = "  " * (3 - max_depth + depth)
                if item.is_dir():
                    tree_str.append(f"{indent}📁 {item.name}/")
                    walk_dir(item, depth + 1)
                else:
                    tree_str.append(f"{indent}📄 {item.name}")
        except PermissionError:
            pass # 忽略没有权限访问的文件夹
            
    walk_dir(SAFE_DIR, 1)
    return "\n".join(tree_str)

def get_function_signature(file_path: str, target_name: str) -> str:
    """工具2：基于 AST 提取特定函数/类的完整代码块"""
    target_path = SAFE_DIR / file_path
    if not target_path.exists() or target_path.suffix != '.py':
        return "Error: 文件不存在或非 Python 文件"

    with open(target_path, 'r', encoding='utf-8') as f:
        source_code = f.read()

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return "Error: 目标文件存在语法错误，AST 解析失败"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == target_name:
                return ast.get_source_segment(source_code, node)
                
    return f"Error: 在 {file_path} 中未找到 {target_name}"

def search_code(keyword: str) -> str:
    """工具3：安全的代码文本检索"""
    results = []
    for root, dirs, files in os.walk(SAFE_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for file in files:
            # FIX 2: 正确的字符串结尾判断
            if file.endswith('.py'):
                file_path = Path(root) / file
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            if keyword in line:
                                snippet = "".join(lines[max(0, i-1):min(len(lines), i+2)])
                                # FIX 2: 直接使用 file 而不是 file.name
                                results.append(f"[{file} Line {i+1}]:\n{snippet.strip()}")
                                if len(results) >= 5: 
                                    return "\n---\n".join(results) + "\n... (更多结果已截断)"
                except Exception:
                    continue
    return "\n---\n".join(results) or "未搜索到结果"