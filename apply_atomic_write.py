import os
import re

# 1. Update dashboard.py
dashboard_path = 'core/dashboard.py'
content = open(dashboard_path, encoding='utf-8').read()

old_except = '''        except Exception:
            st.sidebar.error("读取行情健康状态失败")'''

new_except = '''        except Exception:
            st.sidebar.markdown("**主数据源**: UNKNOWN")
            st.sidebar.markdown("**状态**: 🔴 DOWN")
            st.sidebar.markdown("**延迟**: N/A")
            st.sidebar.markdown("**最后更新**: N/A")'''

content = content.replace(old_except, new_except)
open(dashboard_path, 'w', encoding='utf-8').write(content)

# 2. Update brain_node.py for atomic write and trading hours
brain_node_path = 'brain_node.py'
content = open(brain_node_path, encoding='utf-8').read()

old_write = '''        # === 写入行情源健康状态 ===
        health_data = {}
        try:
            if market_provider:
                health_data = market_provider.health_check()
                with open(os.path.join(PROJECT_ROOT, "data_cache", "market_health.json"), "w", encoding="utf-8") as f:
                    json.dump(health_data, f)
        except Exception as e:
            logger.error(f"写入行情健康状态失败: {e}")'''

new_write = '''        # === 写入行情源健康状态 ===
        health_data = {}
        try:
            if market_provider:
                health_data = market_provider.health_check()
                import tempfile
                health_file_path = os.path.join(PROJECT_ROOT, "data_cache", "market_health.json")
                dir_name = os.path.dirname(health_file_path)
                os.makedirs(dir_name, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(health_data, f)
                os.replace(tmp_path, health_file_path)
        except Exception as e:
            logger.error(f"写入行情健康状态失败: {e}")'''

content = content.replace(old_write, new_write)

open(brain_node_path, 'w', encoding='utf-8').write(content)
