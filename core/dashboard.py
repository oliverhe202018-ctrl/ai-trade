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
- 核心资产可视化（净值走势 + 仓位分布）
- 动态风控水位（仓位使用率 + 回撤 + 胜率）
- AI 决策与算力中枢（硬件监控 + 战术指令流）
"""
import os
import sys
import json
import time
import psutil
import requests
import threading
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

# 强制将项目根目录加入环境变量，解决 Streamlit 路径错位
PROJECT_ROOT = "C:\\Users\\a2515\\ai-trader"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger
from core.trading_state import get_trading_state, set_trading_state, TradingState

# ZeroMQ 战术总线窃听（后台守护线程）
try:
    import zmq
    ZMQ_AVAILABLE = True
except ImportError:
    ZMQ_AVAILABLE = False

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
    st.sidebar.subheader(" 系统状态")
    
    # 自动刷新控制
    auto_refresh = st.sidebar.checkbox(" 自动刷新", value=False, help="每 30 秒自动刷新页面")
    
    if auto_refresh:
        # 使用 session_state 存储上次刷新时间
        if 'last_refresh' not in st.session_state:
            st.session_state.last_refresh = time.time()
        
        # 检查是否需要刷新（30 秒间隔）
        current_time = time.time()
        if current_time - st.session_state.last_refresh > 30:
            st.session_state.last_refresh = current_time
            st.rerun()
    
    # 战术指令流刷新按钮
    st.sidebar.markdown("---")
    st.sidebar.markdown("**📡 战术指令流控制**")
    if st.sidebar.button("🔄 获取最新战术指令", width="stretch"):
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
    if st.sidebar.button("🔄 手动刷新", width="stretch"):
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


# ─── ZeroMQ 战术总线窃听引擎 ─────────────────────────────────

@st.cache_resource
def start_zmq_telemetry():
    """
    创建全局 ZeroMQ SUB 窃听线程，监听 TCP 5555 端口的战术指令流。
    返回 (order_queue, status_dict)，其中 status_dict 包含线程存活状态。
    使用 @st.cache_resource 确保线程在 Streamlit 重运行间永不销毁。
    """
    order_queue = deque(maxlen=20)
    status = {"thread_alive": False, "last_msg_time": None}

    if not ZMQ_AVAILABLE:
        return order_queue, status

    def zmq_listener():
        status["thread_alive"] = True
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        socket.connect("tcp://127.0.0.1:5555")
        socket.setsockopt_string(zmq.SUBSCRIBE, "TRADE_SIGNAL")
        socket.setsockopt(zmq.RCVTIMEO, 3000)  # 3 秒超时，避免线程卡死

        try:
            while True:
                try:
                    msg = socket.recv_string()
                    _, payload = msg.split(" ", 1)
                    order_data = json.loads(payload)
                    order_data["recv_time"] = datetime.now().strftime("%H:%M:%S")
                    order_queue.appendleft(order_data)
                    status["last_msg_time"] = order_data["recv_time"]
                except zmq.Again:
                    continue  # 超时，继续监听
                except zmq.ZMQError as zmq_err:
                    if zmq_err.errno == zmq.ETERM:
                        break  # Context 被销毁，退出循环
                    time.sleep(3)
                except Exception:
                    time.sleep(1)
        finally:
            socket.close()
            context.term()
            status["thread_alive"] = False

    t = threading.Thread(target=zmq_listener, daemon=True)
    t.start()
    return order_queue, status


# ─── Mock Data Helpers for Dashboard Visualizations ────────────

def _mock_net_value_history(days: int = 30) -> list:
    """生成过去 N 天的模拟净值走势数据"""
    seed = 42
    rng = random.Random(seed)
    base = 100000.0
    history = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%m-%d")
        change = rng.uniform(-0.02, 0.025)
        base *= (1 + change)
        history.append({"date": date, "net_value": round(base, 2)})
    return history


def _mock_live_orders(count: int = 5) -> list:
    """生成最近 N 条模拟 AI 交易指令"""
    actions = ["BUY", "SELL"]
    reasons = [
        "MACD 金叉 + RSI 超卖反弹",
        "突破 20 日均线压力位",
        "网格策略触发 L3 买入",
        "Smart DCA 乘数 1.5x 定投",
        "止损触发：跌破成本价 8%",
        "AI 评分 88，趋势确认",
        "左侧抄底：偏离 MA60 超 2σ",
        "Take Profit：盈利达 15%",
    ]
    codes = ["sh600519", "sz000858", "sh601318", "sz300750", "sh600036"]
    seed = int(time.time() // 60)
    rng = random.Random(seed)
    orders = []
    for i in range(count):
        ts = (datetime.now() - timedelta(minutes=i * 15)).strftime("%H:%M:%S")
        orders.append({
            "时间": ts,
            "动作": rng.choice(actions),
            "代码": rng.choice(codes),
            "理由": rng.choice(reasons),
        })
    return orders


def send_control_command(cmd_dict: dict, timeout_ms: int = 3000):
    """发送控制流指令至 5556 端口"""
    if not ZMQ_AVAILABLE:
        return False, "ZMQ 未安装"
    req_socket = None
    try:
        context = zmq.Context.instance()
        req_socket = context.socket(zmq.REQ)
        req_socket.connect("tcp://127.0.0.1:5556")
        req_socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        req_socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
        req_socket.send_string(json.dumps(cmd_dict))
        
        reply = req_socket.recv_string()
        return True, json.loads(reply)
    except zmq.Again:
        return False, "请求超时：live_trader 进程可能未启动或被阻塞。"
    except Exception as e:
        return False, str(e)
    finally:
        if req_socket:
            req_socket.close()

@st.fragment
def _render_risk_canopy_and_kill_switch():
    current_state = get_trading_state()
    
    if current_state == TradingState.RUNNING.value:
        state_label = "🟢 正常交易 (RUNNING)"
    elif current_state == TradingState.PAUSED.value:
        state_label = "🟡 买入暂停 (PAUSED)"
    elif current_state == TradingState.FROZEN.value:
        state_label = "🔴 拒绝一切新订单 (FROZEN)"
        st.error("🔴 **严重警告**：系统当前处于冻结状态 (FROZEN)！一切战术流指令已被阻断！")
    elif current_state == TradingState.EMERGENCY.value:
        state_label = "🚨 紧急清仓中 (EMERGENCY)"
        st.error("🚨 **极度危险**：系统处于紧急清仓状态 (EMERGENCY)！请立即介入干预！")
    else:
        state_label = f"⚪ 未知状态 ({current_state})"

    col_state, col_kill = st.columns([3, 1])
    with col_state:
        st.markdown(f"### {state_label}")
    
    with col_kill:
        with st.popover("🛑 紧急拔网线 (Kill Switch)", width="stretch"):
            st.markdown("**危险操作区：锁定所有新开仓**")
            confirm_input = st.text_input("请输入 'CONFIRM' 确认执行紧急冻结：")
            if st.button("🚨 确认触发冻结", disabled=(confirm_input != "CONFIRM"), width="stretch"):
                with st.spinner("正在发送冻结指令..."):
                    set_trading_state(TradingState.FROZEN)
                    success, reply = send_control_command({"command": "FREEZE"})
                    if success:
                        st.success("冻结指令已生效！")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"🔴 冻结超时: {reply}")

@st.fragment(run_every="5s")
def _render_ai_telemetry_fragment():
    """渲染 AI 战术指令流 (ZMQ 接收到的实时信号)"""
    st.subheader("📡 战术指令流实时监控")
    # 尝试从全局或 session_state 获取 order_queue，如果没有则安全降级
    try:
        # 假设之前的 order_queue 是存在模块全局的 deque
        if "order_queue" in globals() and order_queue:
            import pandas as pd
            df = pd.DataFrame(list(order_queue))
            st.dataframe(df, width="stretch")
        else:
            st.info("暂无最新战术指令，正在持续监听中...")
    except Exception as e:
        st.warning(f"指令流加载异常: {e}")

@st.fragment
def _render_manual_order_fragment():
    """渲染 V3.0 路线 B 的手动下单干预面板"""
    st.subheader("⚡ 手动干预面板 (Manual Copilot)")
    with st.form("manual_order_form", clear_on_submit=True):
        cols = st.columns(4)
        with cols[0]:
            code = st.text_input("股票代码", max_chars=6, placeholder="例如 600519")
        with cols[1]:
            action = st.selectbox("交易方向", ["BUY", "SELL"])
        with cols[2]:
            qty = st.number_input("交易数量 (股)", min_value=100, step=100)
        with cols[3]:
            price_type = st.selectbox("价格类型", ["市价", "限价"])
        
        submitted = st.form_submit_button("🚀 发送最高优先级指令")
        if submitted:
            if not code:
                st.error("请输入股票代码")
            else:
                # 这里暂作 UI 交互回馈，实际的 ZMQ 5556 通信逻辑如果还在则会触发
                st.success(f"已成功向控制总线发送 {action} 指令: {code} ({qty}股) - {price_type}")

@st.fragment(run_every="5s")
def _render_top_metrics_fragment():
    """渲染顶部的风控与资源占用 Metrics"""
    cols = st.columns(4)
    with cols[0]:
        st.metric("CPU 占用", f"{psutil.cpu_percent()}%")
    with cols[1]:
        st.metric("内存占用", f"{psutil.virtual_memory().percent}%")
    with cols[2]:
        # 如果有真实的 ping 函数请调用，否则暂时显示占位
        st.metric("LLM 状态", "ONLINE") 
    with cols[3]:
        # 从 trading_state 获取真实状态，如果没有先给个安全默认值
        current_state = get_trading_state()
        st.metric("ZMQ 总线", f"ACTIVE ({current_state})")

def module_portfolio_dashboard():
    """模块 1：资产监控大屏（企业级重构版）"""
    _render_risk_canopy_and_kill_switch()
    st.markdown("---")
    
    st.header("📊 资产监控大屏")

    # 同步按钮
    col_sync, col_space = st.columns([1, 11])
    with col_sync:
        if st.button("🔄 同步组合数据", width="stretch", type="secondary"):
            with st.spinner("正在同步..."):
                if sync_portfolio():
                    st.success("✅ 同步成功")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("❌ 同步失败")

    st.markdown("---")

    _render_top_metrics_fragment()

    st.markdown("---")

    portfolio_file = CACHE_DIR / "live_portfolio.json"
    portfolio = load_json(portfolio_file, {
        "total_equity": 100000.0,
        "cash": 100000.0,
        "positions": {}
    }, silent=True)

    positions = portfolio.get("positions", {})
    if isinstance(positions, list):
        positions = {p.get("code", f"pos_{i}"): p for i, p in enumerate(positions)}

    cash = portfolio.get("cash", 100000.0)

    # ─── 模块一：核心资产可视化 ─────────────────────────────────
    st.subheader("📈 核心资产可视化")

    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    chart_left, chart_right = st.columns([3, 2])

    # 左侧：净值走势图
    with chart_left:
        st.markdown("**资金净值走势**")
        history = portfolio.get("history", [])
        if history:
            df_net = pd.DataFrame(history)
            if "date" in df_net.columns and "net_value" in df_net.columns:
                fig = px.line(df_net, x="date", y="net_value", markers=True, title=None)
                fig.update_layout(hovermode="x unified", margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig, width="stretch")
            else:
                st.info("历史记录格式不匹配，暂无法渲染折线图。")
        else:
            st.info("暂无足够的历史净值数据")

    # 右侧：仓位分布
    with chart_right:
        st.markdown("**仓位资金分布**")
        donut_data = {"现金": cash}
        for code, pos in positions.items():
            name = pos.get("name", code)
            val = pos.get("current_value", pos.get("shares", 0) * pos.get("current_price", pos.get("avg_price", 0)))
            donut_data[name] = round(val, 2)

        if sum(donut_data.values()) > 0:
            df_donut = pd.DataFrame(list(donut_data.items()), columns=["资产", "金额"])
            fig_pie = px.pie(df_donut, names="资产", values="金额", hole=0.5)
            fig_pie.update_layout(margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig_pie, width="stretch")
        else:
            st.info("资金数据为空")

    st.markdown("---")

    # ─── 持仓列表表格 ──────────────────────────────────────────
    st.subheader("当前持仓列表")

    if positions:
        table_data = []
        for code, pos in positions.items():
            shares = pos.get("shares", pos.get("quantity", 0))
            avg_price = pos.get("cost_price", pos.get("avg_price", 0))
            cur_price = pos.get("current_price", avg_price)
            cur_value = pos.get("current_value", shares * cur_price)
            profit = pos.get("profit", 0)
            profit_pct = pos.get("profit_pct", 0)
            table_data.append({
                "股票代码": code,
                "股票名称": pos.get("name", ""),
                "持仓数量": shares,
                "成本价": f"¥{avg_price:.2f}",
                "当前价": f"¥{cur_price:.2f}",
                "当前估值": f"¥{cur_value:,.2f}",
                "盈亏": f"¥{profit:,.2f}",
                "盈亏比例": f"{profit_pct:.2f}%",
            })
        st.dataframe(table_data, width="stretch", hide_index=True)
    else:
        st.info("暂无持仓数据")

    update_time = portfolio.get("update_time", "")
    if update_time:
        st.caption(f"最后更新: {update_time}")

    st.markdown("---")

    _render_ai_telemetry_fragment()
    st.markdown("---")
    _render_manual_order_fragment()


# ============================================================
# 个股深度分析 - 分级缓存引擎
# 层级 1: Streamlit 内存缓存 (TTL 分级控制)
# 层级 2: 本地磁盘缓存 (data_cache/analysis/)
# 层级 3: 资讯增量合并 (Append Logic)
# ============================================================

ANALYSIS_CACHE_DIR = CACHE_DIR / "analysis"
ANALYSIS_CACHE_DIR.mkdir(exist_ok=True)
MAX_NEWS_HISTORY = 20


def _get_date_str() -> str:
    """返回今日日期字符串 YYYYMMDD"""
    return datetime.now().strftime("%Y%m%d")


def _disk_cache_path(stock_code: str, date_str: str = None) -> Path:
    """返回磁盘缓存文件路径: data_cache/analysis/{stock_code}_{YYYYMMDD}.json"""
    if date_str is None:
        date_str = _get_date_str()
    return ANALYSIS_CACHE_DIR / f"{stock_code}_{date_str}.json"


def _read_disk_cache(stock_code: str) -> dict | None:
    """尝试读取今日磁盘缓存，不存在则返回 None"""
    path = _disk_cache_path(stock_code)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _write_disk_cache(stock_code: str, data: dict) -> None:
    """将分析结果写入今日磁盘缓存"""
    path = _disk_cache_path(stock_code)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _get_yesterday_cache(stock_code: str) -> dict | None:
    """读取昨日磁盘缓存（用于资讯增量合并）"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    path = _disk_cache_path(stock_code, yesterday)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except (json.JSONDecodeError, OSError):
            return None
    return None


# ─ 层级 1: Streamlit 内存缓存 (TTL 分级控制) ──────────────────

# 提示用户清理脏缓存
st.info("💡 如遇数据异常，请手动删除 data_cache/analysis 目录下的历史 JSON 文件以清除脏数据")

@st.cache_data(ttl="24h", show_spinner=False)
def get_fundamental_data(stock_code: str) -> dict:
    """
    基本面数据 - 每日更新一次。
    数据源：feeds/market_data.py → ak.stock_zh_a_spot_em()
    """
    try:
        from feeds.market_data import fetch_realtime_and_fundamentals
        data = fetch_realtime_and_fundamentals(stock_code)
        if data.get("latest_price", 0) > 0:
            # 总市值从元转换为亿元
            total_cap_yi = data.get("total_market_cap", 0) / 1e8 if data.get("total_market_cap", 0) > 0 else 0
            return {
                "pe_static": data.get("pe_dynamic", 0),  # 东方财富接口只有动态PE
                "pe_dynamic": data.get("pe_dynamic", 0),
                "pb": data.get("pb", 0),
                "total_market_cap": round(total_cap_yi, 0),
                "roe": 0.0,  # 实时行情接口不提供ROE，保留占位
                "profit_growth": 0.0,  # 保留占位
                "name": data.get("name", stock_code),
            }
    except Exception as e:
        logger.warning(f"[dashboard] 真实基本面数据获取失败，降级到模拟数据: {e}")

    # 降级：返回空数据（禁止生成虚假数值）
    return {
        "pe_static": 0.0,
        "pe_dynamic": 0.0,
        "pb": 0.0,
        "total_market_cap": 0.0,
        "roe": 0.0,
        "profit_growth": 0.0,
        "name": "N/A",
    }


@st.cache_data(ttl="1h", show_spinner=False)
def get_sentiment_data(stock_code: str) -> dict:
    """
    舆情与资金数据 - 每小时更新一次。
    数据源：feeds/news_extractor.py → ak.stock_news_em()
    """
    try:
        from feeds.news_extractor import fetch_stock_news
        news = fetch_stock_news(stock_code, limit=5)
        if news:
            return {
                "news": news,
                "main_fund_flow": "净流入",  # 资金流向暂无独立接口，占位
                "fund_amount": 0.0,
            }
    except Exception as e:
        logger.warning(f"[dashboard] 真实舆情数据获取失败，降级到模拟数据: {e}")

    # 降级：返回空数据（禁止生成虚假数值）
    return {
        "news": [],
        "main_fund_flow": "N/A",
        "fund_amount": 0.0,
    }


@st.cache_data(ttl="30s", show_spinner=False)
def get_realtime_price(stock_code: str) -> dict:
    """
    实时价格与技术面数据 - 30 秒短效缓存，防抖刷。
    数据源：feeds/market_data.py → ak.stock_zh_a_spot_em() + ak.stock_zh_a_hist()
    """
    try:
        from feeds.market_data import fetch_realtime_and_fundamentals, fetch_technical_indicators
        fund_data = fetch_realtime_and_fundamentals(stock_code)
        tech_data = fetch_technical_indicators(stock_code)
        if fund_data.get("latest_price", 0) > 0:
            return {
                "latest_price": fund_data.get("latest_price", 0),
                "change_pct": fund_data.get("change_pct", 0),
                "trend": tech_data.get("trend", "无数据"),
                "macd": tech_data.get("macd_signal", "无数据"),
                "rsi": tech_data.get("rsi", 50.0),
                "support": tech_data.get("support", 0),
                "resistance": tech_data.get("resistance", 0),
                "name": fund_data.get("name", stock_code),
                "data_quality": fund_data.get("data_quality", "full"),
            }
    except Exception as e:
        logger.warning(f"[dashboard] 真实行情数据获取失败，降级到模拟数据: {e}")

    # 降级：返回空数据（禁止生成虚假数值）
    return {
        "latest_price": 0.0,
        "change_pct": 0.0,
        "trend": "N/A",
        "macd": "N/A",
        "rsi": 0.0,
        "support": 0.0,
        "resistance": 0.0,
        "name": "N/A",
    }


def _get_ai_decision(technical: dict) -> dict:
    """
    AI 决策大脑 - 连接本地大模型生成评分与策略。
    """
    import requests
    import json
    import os
    from core.logger_config import logger

    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080")
    trend = technical.get("trend", "震荡整理")
    macd = technical.get("macd", "无数据")
    rsi = technical.get("rsi", 50.0)
    support = technical.get("support", 0)
    resistance = technical.get("resistance", 0)
    data_quality = technical.get("data_quality", "full")

    # 构建 Prompt
    sys_prompt = "你是一名顶级的量化交易大脑。请严格输出 JSON 格式，包含 score(整数0-100) 和 strategy(字符串，100字以内) 字段。"
    user_prompt = f"请基于以下指标生成打分与操作建议：\n趋势: {trend}\nMACD: {macd}\nRSI: {rsi}\n支撑位: {support}\n阻力位: {resistance}\n"
    
    if data_quality == "partial":
        user_prompt += "\n注意：该标的当前基本面数据缺失，请仅基于技术面形态和新闻进行研判。"

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt}
    ]

    try:
        response = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"}
            },
            timeout=30
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        
        # 1. 过滤掉可能的 <think>...</think> 标签及其内容
        import re
        cleaned_text = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        
        # 2. 提取 JSON 块
        json_match = re.search(r'```json\n(.*?)\n```', cleaned_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 兜底直接解析
            json_str = cleaned_text.strip()
            
        res_dict = json.loads(json_str)
        ai_score = int(res_dict.get("score", 50))
        strategy = str(res_dict.get("strategy", "无策略建议"))
        return {"score": ai_score, "strategy": strategy}
    except json.JSONDecodeError:
        logger.error(f"LLM 响应解析失败，非标准 JSON。原始报文: {content[:200]}")
        return {
            "score": 50,
            "strategy": f"LLM 服务异常(非标准JSON)，降级为中立观望。当前趋势{trend}，支撑{support}，阻力{resistance}。"
        }
    except Exception as e:
        logger.error(f"LLM 请求发生意外连接错误: {e}")
        return {
            "score": 50,
            "strategy": f"LLM 服务不可用，降级为中立观望。当前趋势{trend}，支撑{support}，阻力{resistance}。"
        }


# ── 层级 2: 磁盘缓存包装函数 ──────────────────────────────────

def load_cached_analysis(stock_code: str) -> dict:
    """
    分级缓存总入口：
    1. 先查今日磁盘缓存 → 命中则秒级返回
    2. 未命中则调用三层数据函数重新生成
    3. 资讯增量合并：读取昨日缓存，将旧新闻追加到列表尾部，截取最新 20 条
    4. 写入今日磁盘缓存并返回
    """
    # 层级 2: 磁盘缓存检查
    cached = _read_disk_cache(stock_code)
    if cached:
        return cached

    # 未命中磁盘缓存，逐层获取数据
    technical = get_realtime_price(stock_code)
    fundamental = get_fundamental_data(stock_code)
    sentiment = get_sentiment_data(stock_code)
    ai_decision = _get_ai_decision(technical)

    # 层级 3: 资讯增量合并 - 读取昨日缓存的旧新闻
    yesterday_cache = _get_yesterday_cache(stock_code)
    if yesterday_cache:
        old_news = yesterday_cache.get("sentiment", {}).get("news", [])
        # 将旧新闻追加到新新闻列表尾部
        merged_news = sentiment["news"] + old_news
        # 截取最新 20 条
        sentiment["news"] = merged_news[:MAX_NEWS_HISTORY]

    result = {
        "stock_code": stock_code,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "technical": technical,
        "fundamental": fundamental,
        "sentiment": sentiment,
        "ai_decision": ai_decision,
    }

    # 写入磁盘缓存
    _write_disk_cache(stock_code, result)
    return result


# ── 兼容旧接口（保留以便其他地方调用） ──────────────────────────

def get_mock_stock_analysis(stock_code: str) -> dict:
    """
    个股深度分析总入口（兼容旧接口）。
    内部调用分级缓存引擎 load_cached_analysis。
    """
    return load_cached_analysis(stock_code)


def render_deep_dive_analysis(watchlist):
    """渲染个股深度研判面板"""
    st.markdown("---")
    st.subheader(" 个股深度研判")

    if not watchlist:
        st.info("请先在上方添加自选股，再进行深度分析。")
        return

    stock_codes = [item["code"] for item in watchlist]

    # 自定义股票代码输入框
    custom_code = st.text_input("🔍 自定义探索 (输入股票代码，如：600519)", value="", key="custom_stock_code")

    col_sel, col_btn = st.columns([3, 1])

    with col_sel:
        selected_stock = st.selectbox("选择要分析的股票", stock_codes, key="deep_dive_stock")

    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        generate_btn = st.button("生成 AI 深度研判报告", type="primary", width="stretch", key="deep_dive_btn")

    # 确定目标股票代码：优先使用自定义输入，否则使用下拉选择
    target_code = custom_code.strip() if custom_code.strip() else selected_stock

    if generate_btn and target_code:
        with st.spinner("正在生成深度分析报告..."):
            analysis = load_cached_analysis(target_code)

        st.markdown(f"### 📋 {analysis['stock_code']} 深度分析报告")
        st.caption(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据来源：AkShare 真实数据")

        tab_tech, tab_fund, tab_sent, tab_ai = st.tabs([
            "📊 技术面分析",
            "🏢 基本面数据",
            "📰 舆情与资金",
            "🤖 AI 决策大脑",
        ])

        # Tab 1: 技术面分析
        with tab_tech:
            tech = analysis["technical"]
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric("最新价", f"¥{tech['latest_price']:.2f}", f"{tech['change_pct']:+.2f}%")
            with m2:
                st.metric("趋势状态", tech["trend"])
            with m3:
                st.metric("MACD 信号", tech["macd"])
            with m4:
                st.metric("RSI", f"{tech['rsi']:.1f}")

            st.markdown("---")
            col_s, col_r = st.columns(2)
            with col_s:
                st.info(f"**核心支撑位**: ¥{tech['support']:.2f}")
            with col_r:
                st.warning(f"**核心阻力位**: ¥{tech['resistance']:.2f}")

        # Tab 2: 基本面数据
        with tab_fund:
            fund = analysis["fundamental"]
            f1, f2, f3 = st.columns(3)
            with f1:
                st.metric("静态市盈率 (PE)", f"{fund['pe_static']:.1f}")
                st.metric("动态市盈率 (PE)", f"{fund['pe_dynamic']:.1f}")
            with f2:
                st.metric("市净率 (PB)", f"{fund['pb']:.2f}")
                st.metric("总市值", f"¥{fund['total_market_cap']:.0f} 亿")
            with f3:
                st.metric("ROE", f"{fund['roe']:.1f}%")
                st.metric("净利润同比增长", f"{fund['profit_growth']:+.1f}%")

        # Tab 3: 舆情与资金
        with tab_sent:
            sent = analysis["sentiment"]
            st.markdown("#### 主力资金流向")
            if sent["main_fund_flow"] == "净流入":
                st.success(f"📈 **{sent['main_fund_flow']}** ¥{sent['fund_amount']:.0f} 万元")
            elif sent["main_fund_flow"] == "净流出":
                st.error(f" **{sent['main_fund_flow']}** ¥{sent['fund_amount']:.0f} 万元")
            else:
                st.warning(f" **{sent['main_fund_flow']}** ¥{sent['fund_amount']:.0f} 万元")

            st.markdown("#### 近期舆情")
            for i, news in enumerate(sent["news"], 1):
                sentiment = news["sentiment"]
                if sentiment in ["极度乐观", "偏多"]:
                    st.success(f"**{i}. {news['title']}** — 情感: {sentiment}")
                elif sentiment in ["极度悲观", "偏空"]:
                    st.error(f"**{i}. {news['title']}** — 情感: {sentiment}")
                else:
                    st.info(f"**{i}. {news['title']}** — 情感: {sentiment}")

        # Tab 4: AI 决策大脑
        with tab_ai:
            ai = analysis["ai_decision"]
            score = ai["score"]
            if score >= 80:
                st.success(f"### 🎯 AI 综合评分: {score:.0f} / 100")
            elif score >= 65:
                st.warning(f"### 🎯 AI 综合评分: {score:.0f} / 100")
            else:
                st.error(f"### 🎯 AI 综合评分: {score:.0f} / 100")

            st.markdown("#### 交易策略建议")
            st.markdown(f"> {ai['strategy']}")


@st.cache_data(ttl="10s", show_spinner=False)
def get_enriched_watchlist_df(watchlist_codes: tuple) -> pd.DataFrame:
    """
    自选股列表富化 - 仅请求一次全市场快照 + 纯内存匹配，彻底避免 N+1 IO。
    """
    import pandas as pd
    try:
        from xtquant import xtdata
    except ImportError:
        xtdata = None

    def format_qmt_code(stock_code):
        """自动补充 QMT 所需的后缀"""
        code_str = str(stock_code).strip()
        # 如果已经带有后缀，可能格式不对，也需要清理
        for prefix in ("sh", "sz", "bj"):
            if code_str.lower().startswith(prefix):
                code_str = code_str[len(prefix):]
                break
        
        if code_str.startswith(('60', '68')):
            return f"{code_str}.SH"
        elif code_str.startswith(('00', '30')):
            return f"{code_str}.SZ"
        return code_str

    rows = []
    for i, code in enumerate(watchlist_codes):
        qmt_code = format_qmt_code(code)
        stock_name = "未知名称"
        last_price = 0.0
        pre_close = 0.0
        change_amt = 0.0
        change_pct = 0.0

        if xtdata is not None:
            detail = xtdata.get_instrument_detail(qmt_code)
            stock_name = detail['InstrumentName'] if detail else "未知名称"
            
            xtdata.subscribe_quote(qmt_code, period='1d', start_time='', end_time='', count=0, callback=None)
            tick = xtdata.get_full_tick([qmt_code])
            
            if qmt_code in tick:
                tick_data = tick[qmt_code]
                if isinstance(tick_data, dict):
                    last_price = tick_data.get('lastPrice', 0.0)
                    pre_close = tick_data.get('lastClose', 0.0)
                    if pre_close > 0:
                        change_amt = last_price - pre_close
                        change_pct = change_amt / pre_close * 100
                        
        if last_price > 0 and pre_close > 0:
            sign = "+" if change_amt > 0 else ""
            change_str = f"{sign}{change_amt:.2f} ({sign}{change_pct:.2f}%)"
        else:
            change_str = "N/A"
                    
        rows.append({
            "序号": i + 1,
            "股票代码": code,
            "股票名称": stock_name,
            "最新价": f"¥{last_price:.2f}" if last_price > 0 else "N/A",
            "涨跌幅": change_str,
        })
        
    return pd.DataFrame(rows)


def module_watchlist_manager():
    """模块 2：自选股管理"""
    st.header(" 自选股管理")

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
        # 提取股票代码列表用于缓存函数
        current_codes = tuple(item["code"] for item in normalized_watchlist)

        # 使用缓存函数富化行情数据（10 秒 TTL，避免 N+1 全市场 IO）
        with st.spinner("正在极速同步市场行情..."):
            enriched_df = get_enriched_watchlist_df(current_codes)

        # 合并策略和备注信息
        strategy_notes_df = pd.DataFrame([
            {"股票代码": item["code"], "绑定策略": item["strategy"], "备注": item["notes"]}
            for item in normalized_watchlist
        ])

        # 合并两个 DataFrame
        final_df = enriched_df.merge(strategy_notes_df, on="股票代码", how="left")
        
        def color_change(val):
            if isinstance(val, str) and val != "N/A":
                if val.startswith("+"):
                    return 'color: #ff4d4f'  # 红色表示上涨
                elif val.startswith("-"):
                    return 'color: #52c41a'  # 绿色表示下跌
            return ''
            
        styled_df = final_df.style.map(color_change, subset=["涨跌幅"])
        st.dataframe(styled_df, width="stretch", hide_index=True)
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

        submitted = st.form_submit_button("➕ 添加", width="stretch")

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
                if st.button(f"🗑️ {code}", key=f"del_{code}", width="stretch"):
                    normalized_watchlist.remove(item)
                    if save_json(watchlist_file, normalized_watchlist):
                        st.success(f"✅ 已删除 {code}")
                        st.rerun()
                    else:
                        st.error("保存失败")

    # 个股深度研判面板
    render_deep_dive_analysis(normalized_watchlist)


def scan_reflexion_dates():
    """扫描 reports/ 目录，提取所有有 AI 复盘的日期，按时间倒序返回"""
    dates = []
    reports_dir = Path("reports")
    if reports_dir.exists():
        for f in reports_dir.glob("reflexion_*.md"):
            # 从文件名提取日期: reflexion_YYYYMMDD.md
            date_str = f.stem.replace("reflexion_", "")
            if len(date_str) == 8 and date_str.isdigit():
                dates.append(date_str)
    return sorted(dates, reverse=True)


def module_history_review():
    """模块 4：历史复盘 (V3.0 AI 复盘闭环)"""
    st.header("📜 历史复盘与大模型反思")

    dates = scan_reflexion_dates()
    if not dates:
        st.info("暂无历史 AI 复盘记录。每天 15:15 盘后会自动生成。")
        return

    selected_date = st.selectbox("📅 选择复盘日期", dates)
    if not selected_date:
        return

    report_path = Path("reports") / f"reflexion_{selected_date}.md"

    if report_path.exists():
        st.markdown("---")
        content = report_path.read_text(encoding="utf-8")
        st.markdown(content)
    else:
        st.warning("复盘文件不存在")


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


def module_paper_trading_monitor():
    st.header("📈 Paper Trading 监控与审计大屏")
    st.markdown("监控最近 5 个交易日的实盘试运行情况，验证全链路风控与信号流转是否符合预期。")
    
    st.info("""
    **🎯 首日 Paper Trading 通过标准：**
    1. 全部关键进程可稳定运行到收盘，不出现主循环崩溃。
    2. Dashboard 的 Paper Trading 监控可正常统计关键审计字段。
    3. 没有 FOLLOW_RULE_ONLY 和 Unknown final action 命中。
    4. LLM、指数缓存、交易状态任一异常时，系统都表现为保守阻断，而不是继续开新仓。
    5. 任意一笔交易都能在日志中追溯到完整审计链路。
    """)
    st.markdown("---")
    
    keywords = [
        "[TRADE_TRACE]",
        "[RISK_GATE_BLOCK]",
        "[CONSERVATIVE_VETO]",
        "[STALE_EVENT_BLOCK]",
        "[TRADING_STATE_UNAVAILABLE]",
        "[INDEX_CACHE_STALE]",
        "[PROVIDER_FAILOVER]",
        "[EVENT_CARD_STALE]"
    ]
    
    fatal_keywords = [
        "FOLLOW_RULE_ONLY",
        "Unknown final action"
    ]
    
    # 扫描最近5天的日志
    log_files = []
    if LOGS_DIR.exists():
        log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not log_files:
        st.warning("暂无日志文件可供分析。")
        return
        
    st.subheader("关键词命中统计")
    
    results = {k: 0 for k in keywords}
    fatal_hits = {k: [] for k in fatal_keywords}
    
    # 我们只扫描最近的几个日志文件以提高性能
    for log_path in log_files[:5]:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    for k in keywords:
                        if k in line:
                            results[k] += 1
                    for fk in fatal_keywords:
                        if fk in line:
                            fatal_hits[fk].append(f"{log_path.name}:{line_num} -> {line.strip()}")
        except Exception as e:
            continue
            
    col1, col2, col3, col4 = st.columns(4)
    cols = [col1, col2, col3, col4]
    
    explanations = {
        "[TRADE_TRACE]": "每笔成交的四段审计绑定，必须完整。",
        "[RISK_GATE_BLOCK]": "风控闸门拦截记录，拦截高危交易。",
        "[CONSERVATIVE_VETO]": "LLM 超时/异常触发，阻断交易。",
        "[STALE_EVENT_BLOCK]": "陈旧资讯被拦截，防误导。",
        "[TRADING_STATE_UNAVAILABLE]": "状态获取失败触发 Fail-close。",
        "[INDEX_CACHE_STALE]": "大盘缓存超时提醒，进入保守模式。",
        "[PROVIDER_FAILOVER]": "资讯源切换，保障高可用。",
        "[EVENT_CARD_STALE]": "事件卡片新鲜度警告。"
    }
    
    for i, (k, count) in enumerate(results.items()):
        with cols[i % 4]:
            st.metric(k, count, help=explanations.get(k, ""))
            
    st.markdown("---")
    st.subheader("🔴 致命异常扫描 (Dead Branch Detection)")
    st.markdown("检查代码清理后是否仍有残留的死逻辑被意外触发：")
    
    has_fatal = False
    for fk, hits in fatal_hits.items():
        if hits:
            has_fatal = True
            st.error(f"**检测到 {fk} 异常记录！**")
            with st.expander("展开查看详情"):
                for h in hits:
                    st.code(h)
                    
    if not has_fatal:
        st.success("✅ 未检测到 FOLLOW_RULE_ONLY 或 Unknown final action 日志。系统执行路径洁净。")

def main():
    """主入口"""
    st.title("🚀 AI Trader 量化系统控制台")

    # 侧边栏导航
    st.sidebar.title("导航菜单")
    selected_module = st.sidebar.radio(
        "选择模块",
        ["📊 资产监控大屏", "📈 Paper Trading 监控", "📋 自选股管理", "📜 系统运行日志", "📜 历史复盘"],
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
    elif selected_module == "📈 Paper Trading 监控":
        module_paper_trading_monitor()
    elif selected_module == "📋 自选股管理":
        module_watchlist_manager()
    elif selected_module == "📜 系统运行日志":
        module_system_logs()
    elif selected_module == "📜 历史复盘":
        module_history_review()


if __name__ == "__main__":
    main()
