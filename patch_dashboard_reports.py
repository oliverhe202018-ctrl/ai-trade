import os

content = open('core/dashboard.py', encoding='utf-8').read()

new_module_code = """
def module_observation_reports():
    st.header("👀 72 小时并行观察报告")
    st.markdown("这里汇集了最新一轮脚本自动扫描生成的报告文件。如果文件不存在请确保运行了 `scripts/run_72h_observation.py`。")
    
    report_files = {
        "📊 总体状态报告 (observation_72h_status.md)": os.path.join(PROJECT_ROOT, "reports", "observation_72h_status.md"),
        "📈 模拟盘观察报告 (paper_trading_observation.md)": os.path.join(PROJECT_ROOT, "reports", "paper_trading_observation.md"),
        "📰 资讯只读观察报告 (news_readonly_observation.md)": os.path.join(PROJECT_ROOT, "reports", "news_readonly_observation.md")
    }
    
    for title, path in report_files.items():
        with st.expander(title, expanded=False):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        st.markdown(f.read())
                except Exception as e:
                    st.error(f"读取失败: {e}")
            else:
                st.warning(f"报告文件未生成: {path}")

"""

# Insert before def main():
content = content.replace("def main():", new_module_code + "def main():")

# Update radio options
old_radio = """["📊 资产监控大屏", "📰 资讯情绪中枢", "📈 Paper Trading 监控", "📋 自选股管理", "📜 系统运行日志", "📜 历史复盘"]"""
new_radio = """["📊 资产监控大屏", "📰 资讯情绪中枢", "📈 Paper Trading 监控", "📋 自选股管理", "📜 系统运行日志", "📜 历史复盘", "👀 72小时观察报告"]"""
content = content.replace(old_radio, new_radio)

# Update if/elif block
old_elif = """    elif selected_module == "📜 历史复盘":
        module_history_review()"""
new_elif = """    elif selected_module == "📜 历史复盘":
        module_history_review()
    elif selected_module == "👀 72小时观察报告":
        module_observation_reports()"""
content = content.replace(old_elif, new_elif)

open('core/dashboard.py', 'w', encoding='utf-8').write(content)
