import os
import sys
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

def run_grep_audit():
    print("=" * 60)
    print("1. 直接数据源解耦审计 (Grep Audit)")
    print("=" * 60)
    
    targets = ["brain_node.py", "live_trader.py"]
    keywords = ["akshare", "tushare", "xtdata"]
    
    passed = True
    for target in targets:
        if not os.path.exists(target):
            continue
        with open(target, 'r', encoding='utf-8') as f:
            content = f.read()
            for kw in keywords:
                if kw in content:
                    print(f"❌ 警告: 在 {target} 中发现残留的数据源硬编码 '{kw}'")
                    passed = False
    if passed:
        print("✅ 完美解耦！未在 brain_node.py 和 live_trader.py 中发现 akshare/tushare/xtdata 调用。")
        
def run_py_compile():
    print("\n" + "=" * 60)
    print("2. 语法静态审计 (py_compile)")
    print("=" * 60)
    
    files_to_compile = [
        "feeds/base_market_provider.py",
        "feeds/qmt_market_provider.py",
        "brain_node.py",
        "live_trader.py",
        "core/dashboard.py",
        "tests/test_market_provider_fault_injection.py",
        "tests/test_live_trader_market_guard.py",
        "tests/test_brain_node_market_guard.py"
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
    print("3. 行情主链路故障注入测试 (Unittest)")
    print("=" * 60)
    
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    
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
