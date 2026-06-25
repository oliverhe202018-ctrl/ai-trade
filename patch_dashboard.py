import os

content = open('core/dashboard.py', encoding='utf-8').read()

new_news_section = """
    st.sidebar.markdown("---")
    st.sidebar.subheader("📰 资讯源健康度")
    news_health_path = os.path.join(CACHE_DIR, "news_health.json")
    if os.path.exists(news_health_path):
        try:
            import json
            with open(news_health_path, "r", encoding="utf-8") as f:
                news_health = json.load(f)
                
            for p_name, p_data in news_health.get("providers", {}).items():
                p_status = p_data.get("status", "UNKNOWN")
                p_status_color = "🟢" if p_status == "OK" else "🔴" if p_status == "DOWN" else "🟡"
                st.sidebar.markdown(f"**{p_name.upper()}**: {p_status_color} {p_status}")
                
            st.sidebar.markdown(f"**最后更新**: {news_health.get('datetime', 'N/A')}")
        except Exception:
            st.sidebar.markdown("**状态**: 🔴 解析失败 (UNKNOWN)")
    else:
        st.sidebar.markdown("**状态**: 🟡 未初始化")
        
"""

# Insert before System Info section
target_str = '    # 系统信息\n    st.sidebar.markdown("---")\n    st.sidebar.markdown("### ℹ️ 系统信息")'
content = content.replace(target_str, new_news_section + target_str)

open('core/dashboard.py', 'w', encoding='utf-8').write(content)
