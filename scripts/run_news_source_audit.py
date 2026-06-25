import os
import sys
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

def run_grep_audit():
    print("=" * 60)
    print("1. 资讯源交易隔离审计 (Grep Audit)")
    print("=" * 60)
    
    # 查找 feeds 目录下所有带 news 的文件
    news_files = []
    for root, dirs, files in os.walk("feeds"):
        for file in files:
            if "news" in file and file.endswith(".py"):
                news_files.append(os.path.join(root, file))
                
    keywords = ["place_order", "submit_order", "buy", "sell", "TradingState", "set_trading_state"]
    passed = True
    for target in news_files:
        with open(target, 'r', encoding='utf-8') as f:
            content = f.read()
            # 简单检查，忽略注释和仅作为字符串的内容，但这里的检查可以粗略一点，发现直接警告
            for kw in keywords:
                if kw in content:
                    print(f"❌ 警告: 在 {target} 中发现可能干预交易的关键字 '{kw}'")
                    passed = False
    if passed:
        print("✅ 隔离审计通过！未在 news 模块中发现影响交易状态机或直接下单的关键字。")
        
def run_py_compile():
    print("\n" + "=" * 60)
    print("2. 语法静态审计 (py_compile)")
    print("=" * 60)
    
    files_to_compile = [
        "feeds/base_news_provider.py",
        "feeds/cninfo_news_provider.py",
        "feeds/cls_news_provider.py",
        "feeds/news_event_store.py",
        "feeds/news_event_bus.py",
        "feeds/news_extractor.py",
        "core/dashboard.py",
        "tests/test_news_provider_fault_injection.py",
        "tests/test_news_event_store.py"
    ]
    
    passed = True
    for f in files_to_compile:
        if os.path.exists(f):
            result = subprocess.run([sys.executable, "-m", "py_compile", f], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"❌ {f} 编译失败:\n{result.stderr}")
                passed = False
            else:
                print(f"✅ {f} 编译通过。")
        else:
            print(f"⚠️ {f} 不存在，跳过。")
            
def run_unit_tests():
    print("\n" + "=" * 60)
    print("3. 资讯源边界测试与 SQLite 测试 (Unittest)")
    print("=" * 60)
    
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_news_*.py")
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("✅ 所有故障注入与边界拦截测试通过！")
    else:
        print("❌ 存在未通过的故障注入测试！")

if __name__ == "__main__":
    run_grep_audit()
    run_py_compile()
    run_unit_tests()
