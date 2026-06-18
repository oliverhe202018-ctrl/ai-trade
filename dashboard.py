"""
量化交易系统本地可视化控制台 - 增强版
模块 1：资产监控大屏 (Portfolio Dashboard)
模块 2：自选股管理 (Watchlist Manager)
模块 3：系统运行日志 (System Logs)

增强功能：
- 侧边栏系统状态面板 + 自动刷新
- 数据源健康度指示器
- 资产大屏组合同步按钮
- 主题/布局/响应式优化
"""
import os
import json
import time
import streamlit as st
from datetime import datetime
from pathlib import Path

# 配置页面
st.set_page_config(
    page_title="AI Trader Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义 CSS 样式 - 支持明暗主题自适应
st.markdown("""
<style>
    /* 主容器内边距 */
    .main .block-container {
        padding: 2rem 2rem 2rem 2rem;
        max-width: 1400px;
    }
    
    /* 指标卡片样式增强 - 使用主题变量 */
    [data-testid="stMetric"] {
        background-color: var(--secondary-background-color);
        border: 1px solid var(--secondary-background-color);
        padding: 1rem;
        border-radius: 0.5rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    
    [data-testid="stMetricLabel"] {
        font-weight: 600;
        color: var(--text-color);
    }
    
    [data-testid="stMetricValue"] {
        font-size: 1.8rem;
        font-weight: 700;
        color: var(--text-color);
    }
    
    /* 状态指示器 */
    .status-indicator {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        margin-right: 8px;
    }
    
    .status-healthy {
        background-color: #28a745;
        box-shadow: 0 0 8px #28a745;
    }
    
    .status-warning {
        background-color: #ffc107;
        box-shadow: 0 0 8px #ffc107;
    }
    
    .status-error {
        background-color: #dc3545;
        box-shadow: 0 0 8px #dc3545;
    }
    
    /* 数据表格样式 */
    .stDataFrame {
        border-radius: 0.5rem;
        overflow: hidden;
    }
    
    /* 侧边栏样式 - 使用主题变量自适应 */
    [data-testid="stSidebar"] {
        background: var(--secondary-background-color);
        color: var(--text-color) !important;
    }
    
    /* 强制侧边栏内所有元素继承主题文字颜色 */
    [data-testid="stSidebar"] * {
        color: inherit;
    }
    
    /* 侧边栏标题和标签 */
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] label {
        color: var(--text-color) !important;
    }
    
    /* 按钮样式增强 */
    .stButton > button {
        border-radius: 0.5rem;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    
    /* 成功/警告/错误消息样式 */
    .stSuccess, .stWarning, .stError, .stInfo {
        border-radius: 0.5rem;
        padding: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# 数据缓存目录
CACHE_DIR = Path("./data_cache")
LOGS_DIR = Path("./logs")
PORTFOLIO_FILE = Path("./portfolio.json")

# 确保目录存在
CACHE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


def load_json(file_path, default=None, silent=False):
    """安全加载 JSON 文件，失败时返回默认值。
    silent=True 时不显示任何提示框（用于首次启动等预期内的缺失场景）。
    """
    if default is None:
        default = {}
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default
                return json.loads(content)
        # 文件不存在：静默返回默认值
    except (json.JSONDecodeError, PermissionError, OSError):
        if not silent:
            st.warning(f"⚠️ 读取 {os.path.basename(file_path)} 异常，使用默认值")
    return default


def save_json(file_path, data):
    """安全保存 JSON 文件"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"保存文件失败: {file_path} - {e}")
        return False


def get_file_status(file_path):
    """获取文件状态信息"""
    if not os.path.exists(file_path):
        return {"exists": False, "size": 0, "modified": None, "status": "error"}
    
    stat = os.stat(file_path)
    modified_time = datetime.fromtimestamp(stat.st_mtime)
    
    # 判断状态：1小时内更新为健康，24小时内为警告，否则为错误
    now = datetime.now()
    age_hours = (now - modified_time).total_seconds() / 3600
    
    if age_hours < 1:
        status = "healthy"
    elif age_hours < 24:
        status = "warning"
    else:
        status = "error"
    
    return {
        "exists": True,
        "size": stat.st_size,
        "modified": modified_time,
        "status": status,
        "age_hours": age_hours
    }


def render_status_indicator(status, label):
    """渲染状态指示器"""
    status_class = f"status-{status}"
    st.markdown(
        f'<span class="status-indicator {status_class}"></span>{label}',
        unsafe_allow_html=True
    )


def sidebar_status_panel():
    """侧边栏系统状态面板"""
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔧 系统状态")
    
    # 自动刷新控制
    auto_refresh = st.sidebar.checkbox("🔄 自动刷新", value=False, help="每 30 秒自动刷新页面")
    
    if auto_refresh:
        # 使用 session_state 存储上次刷新时间
        if 'last_refresh' not in st.session_state:
            st.session_state.last_refresh = time.time()
        
        # 检查是否需要刷新（30秒间隔）
        current_time = time.time()
        if current_time - st.session_state.last_refresh > 30:
            st.session_state.last_refresh = current_time
            st.rerun()
    
    # 数据源健康度检查
    st.sidebar.markdown("### 📊 数据源健康度")
    
    # 检查各个数据文件
    data_sources = [
        ("live_portfolio.json", CACHE_DIR / "live_portfolio.json", "资产数据"),
        ("custom_watchlist.json", CACHE_DIR / "custom_watchlist.json", "自选股"),
        ("portfolio.json", PORTFOLIO_FILE, "交易组合"),
    ]
    
    for name, path, label in data_sources:
        status = get_file_status(path)
        if status["exists"]:
            modified_str = status["modified"].strftime("%H:%M:%S")
            st.sidebar.markdown(
                f'<span class="status-indicator status-{status["status"]}"></span>'
                f'<b>{label}</b>: {modified_str}',
                unsafe_allow_html=True
            )
        else:
            st.sidebar.markdown(
                f'<span class="status-indicator status-error"></span>'
                f'<b>{label}</b>: 未找到',
                unsafe_allow_html=True
            )
    
    # 系统信息
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ℹ️ 系统信息")
    st.sidebar.markdown(f"**当前时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.sidebar.markdown(f"**工作目录**: {os.getcwd()}")
    
    # 刷新按钮
    if st.sidebar.button("🔄 手动刷新", use_container_width=True):
        st.rerun()


def sync_portfolio():
    """同步 portfolio.json 到 live_portfolio.json"""
    try:
        # 读取主 portfolio.json
        portfolio = load_json(PORTFOLIO_FILE, silent=True)
        
        if not portfolio:
            st.error("无法读取 portfolio.json")
            return False
        
        # 转换为 live_portfolio 格式
        live_portfolio = {
            "total_equity": portfolio.get("cash", 0),
            "cash": portfolio.get("cash", 0),
            "positions": {},
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # 计算持仓市值（这里简化处理，实际应该获取实时价格）
        positions = portfolio.get("position", {})
        total_position_value = 0
        
        for code, pos_data in positions.items():
            shares = pos_data.get("shares", 0)
            avg_price = pos_data.get("avg_price", 0)
            current_value = shares * avg_price  # 简化：使用成本价作为当前价
            total_position_value += current_value
            
            live_portfolio["positions"][code] = {
                "name": pos_data.get("name", code),
                "shares": shares,
                "cost_price": avg_price,
                "current_price": avg_price,  # 简化处理
                "current_value": current_value,
                "profit": 0,  # 需要实时价格才能计算
                "profit_pct": 0
            }
        
        live_portfolio["total_equity"] = portfolio.get("cash", 0) + total_position_value
        
        # 保存到 live_portfolio.json
        if save_json(CACHE_DIR / "live_portfolio.json", live_portfolio):
            return True
        return False
        
    except Exception as e:
        st.error(f"同步失败: {e}")
        return False


def module_portfolio_dashboard():
    """模块 1：资产监控大屏"""
    st.header("📊 资产监控大屏")
    
    # 同步按钮
    col_sync, col_space = st.columns([1, 11])
    with col_sync:
        if st.button("🔄 同步组合数据", use_container_width=True, type="secondary"):
            with st.spinner("正在同步..."):
                if sync_portfolio():
                    st.success("✅ 同步成功")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("❌ 同步失败")
    
    st.markdown("---")

    portfolio_file = CACHE_DIR / "live_portfolio.json"
    portfolio = load_json(portfolio_file, {
        "total_equity": 100000.0,
        "cash": 100000.0,
        "positions": {}
    }, silent=True)

    # 大号字体展示核心指标
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            label="💰 总资产 (Total Equity)",
            value=f"¥{portfolio.get('total_equity', 0):,.2f}",
            delta=None
        )

    with col2:
        st.metric(
            label="💵 可用资金 (Cash)",
            value=f"¥{portfolio.get('cash', 0):,.2f}",
            delta=None
        )

    with col3:
        positions = portfolio.get("positions", {})
        # positions 可能是 dict 或 list，统一处理
        if isinstance(positions, dict):
            pos_count = len(positions)
        else:
            pos_count = len(positions)
        st.metric(
            label="📦 持仓数量",
            value=f"{pos_count} 只",
            delta=None
        )

    st.markdown("---")

    # 持仓列表表格
    st.subheader("当前持仓列表")

    if positions:
        # 构建表格数据，兼容 dict 和 list 两种格式
        table_data = []
        if isinstance(positions, dict):
            # dict 格式: {"sh600519": {"name": "贵州茅台", "shares": 100, ...}}
            for code, pos in positions.items():
                table_data.append({
                    "股票代码": code,
                    "股票名称": pos.get("name", ""),
                    "持仓数量": pos.get("shares", pos.get("quantity", 0)),
                    "成本价": f"¥{pos.get('cost_price', pos.get('avg_price', 0)):.2f}",
                    "当前价": f"¥{pos.get('current_price', 0):.2f}",
                    "当前估值": f"¥{pos.get('current_value', 0):,.2f}",
                    "盈亏": f"¥{pos.get('profit', 0):,.2f}",
                    "盈亏比例": f"{pos.get('profit_pct', 0):.2f}%"
                })
        else:
            # list 格式: [{"code": "sh600519", "name": "贵州茅台", ...}]
            for pos in positions:
                table_data.append({
                    "股票代码": pos.get("code", ""),
                    "股票名称": pos.get("name", ""),
                    "持仓数量": pos.get("quantity", 0),
                    "成本价": f"¥{pos.get('cost_price', 0):.2f}",
                    "当前价": f"¥{pos.get('current_price', 0):.2f}",
                    "当前估值": f"¥{pos.get('current_value', 0):,.2f}",
                    "盈亏": f"¥{pos.get('profit', 0):,.2f}",
                    "盈亏比例": f"{pos.get('profit_pct', 0):.2f}%"
                })

        st.dataframe(
            table_data,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("暂无持仓数据")

    # 最后更新时间
    update_time = portfolio.get("update_time", "")
    if update_time:
        st.caption(f"最后更新: {update_time}")


def module_watchlist_manager():
    """模块 2：自选股管理"""
    st.header("📋 自选股管理")

    watchlist_file = CACHE_DIR / "custom_watchlist.json"
    watchlist = load_json(watchlist_file, [], silent=True)

    # 兼容旧格式：如果列表元素是字符串，转换为字典格式
    normalized_watchlist = []
    for item in watchlist:
        if isinstance(item, str):
            normalized_watchlist.append({"code": item, "strategy": "auto", "notes": ""})
        elif isinstance(item, dict):
            normalized_watchlist.append({
                "code": item.get("code", ""),
                "strategy": item.get("strategy", "auto"),
                "notes": item.get("notes", "")
            })

    # 展示当前自选股列表
    st.subheader(f"当前自选股 ({len(normalized_watchlist)} 只)")

    if normalized_watchlist:
        # 使用 DataFrame 展示
        table_data = []
        for i, item in enumerate(normalized_watchlist):
            table_data.append({
                "序号": i + 1,
                "股票代码": item["code"],
                "绑定策略": item["strategy"],
                "备注": item["notes"]
            })
        st.dataframe(
            table_data,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("自选股列表为空，请添加股票代码")

    st.markdown("---")

    # 添加自选股表单
    st.subheader("添加自选股")

    with st.form("add_stock_form", clear_on_submit=True):
        col1, col2, col3 = st.columns([2, 2, 2])

        with col1:
            stock_code = st.text_input(
                "股票代码 (格式: sh600519 或 sz000001)",
                placeholder="例如: sh600519",
                key="stock_code_input"
            )

        with col2:
            strategy = st.selectbox(
                "绑定策略",
                options=["auto", "grid", "smart_dca", "trend"],
                key="strategy_select"
            )

        with col3:
            notes = st.text_input(
                "备注",
                placeholder="例如: 中线底仓",
                key="notes_input"
            )

        submitted = st.form_submit_button("➕ 添加", use_container_width=True)

        if submitted:
            if not stock_code:
                st.warning("请输入股票代码")
            else:
                # 检查是否已存在
                existing_codes = [item["code"] for item in normalized_watchlist]
                if stock_code in existing_codes:
                    st.warning(f"股票 {stock_code} 已在自选股中")
                else:
                    new_item = {
                        "code": stock_code,
                        "strategy": strategy,
                        "notes": notes
                    }
                    normalized_watchlist.append(new_item)
                    if save_json(watchlist_file, normalized_watchlist):
                        st.success(f"✅ 已添加 {stock_code} (策略: {strategy})")
                        st.rerun()
                    else:
                        st.error("保存失败")

    # 删除按钮
    if normalized_watchlist:
        st.markdown("---")
        st.markdown("**删除自选股:**")

        cols = st.columns(min(len(normalized_watchlist), 4))
        for i, item in enumerate(normalized_watchlist):
            col_idx = i % len(cols)
            code = item["code"]
            with cols[col_idx]:
                if st.button(f"🗑️ {code}", key=f"del_{code}", use_container_width=True):
                    normalized_watchlist.remove(item)
                    if save_json(watchlist_file, normalized_watchlist):
                        st.success(f"✅ 已删除 {code}")
                        st.rerun()
                    else:
                        st.error("保存失败")


def module_system_logs():
    """模块 3：系统运行日志"""
    st.header("📜 系统运行日志")

    # 获取日志文件列表
    log_files = []
    if LOGS_DIR.exists():
        log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
        jsonl_files = sorted(LOGS_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
        log_files.extend(jsonl_files)

    if not log_files:
        st.info("暂无日志文件")
        return

    # 日志文件选择器
    log_file_names = [f.name for f in log_files]
    selected_log = st.selectbox("选择日志文件", log_file_names)

    if selected_log:
        log_path = LOGS_DIR / selected_log

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total = len(all_lines)
            # 直接读取最后 200 行，安全处理行数不足的情况
            tail_lines = all_lines[-200:] if total > 200 else all_lines
            st.subheader(f"最近日志 (文件共 {total} 行，展示最后 {len(tail_lines)} 行)")

            if selected_log.endswith(".jsonl"):
                # JSONL 格式，逐行解析并美化显示
                formatted = []
                for line in reversed(tail_lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        action = entry.get("action", "")
                        code = entry.get("code", "")
                        name = entry.get("name", "")
                        price = entry.get("price", 0)
                        quantity = entry.get("quantity", 0)
                        profit = entry.get("profit", 0)
                        formatted.append(
                            f"{ts}  {action:4s}  {code} {name}  "
                            f"价格:{price:.2f}  数量:{quantity}  盈亏:{profit:,.2f}"
                        )
                    except json.JSONDecodeError:
                        formatted.append(line)
                st.code("\n".join(formatted), language="text")
            else:
                # 普通文本日志，反转显示最新在前
                log_text = "".join(reversed(tail_lines))
                st.code(log_text, language="text")

        except Exception as e:
            st.error(f"读取日志失败: {e}")


def main():
    """主入口"""
    st.title("🚀 AI Trader 量化系统控制台")

    # 侧边栏导航
    st.sidebar.title("导航菜单")
    selected_module = st.sidebar.radio(
        "选择模块",
        ["📊 资产监控大屏", "📋 自选股管理", "📜 系统运行日志"],
        index=0
    )

    # 侧边栏系统状态面板
    sidebar_status_panel()

    st.sidebar.markdown("---")
    st.sidebar.info(
        "💡 **提示**\n\n"
        "- 资产数据来自 `data_cache/live_portfolio.json`\n"
        "- 自选股配置保存在 `data_cache/custom_watchlist.json`\n"
        "- 系统日志位于 `logs/` 目录\n"
        "- 点击'同步组合数据'按钮更新资产信息"
    )

    # 根据选择显示对应模块
    if selected_module == "📊 资产监控大屏":
        module_portfolio_dashboard()
    elif selected_module == "📋 自选股管理":
        module_watchlist_manager()
    elif selected_module == "📜 系统运行日志":
        module_system_logs()


if __name__ == "__main__":
    main()
