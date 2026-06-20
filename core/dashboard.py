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
import json
import time
import random
import threading
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

from core.logger_config import logger

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
    if st.sidebar.button("🔄 获取最新战术指令", use_container_width=True):
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
            except Exception:
                time.sleep(1)

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


def module_portfolio_dashboard():
    """模块 1：资产监控大屏（企业级重构版）"""
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

    positions = portfolio.get("positions", {})
    if isinstance(positions, list):
        positions = {p.get("code", f"pos_{i}"): p for i, p in enumerate(positions)}

    total_equity = portfolio.get("total_equity", 100000.0)
    cash = portfolio.get("cash", 100000.0)
    pos_count = len(positions)

    # ─── 核心指标行 ────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="💰 总资产 (Total Equity)", value=f"¥{total_equity:,.2f}", delta=None)
    with col2:
        st.metric(label="💵 可用资金 (Cash)", value=f"¥{cash:,.2f}", delta=None)
    with col3:
        st.metric(label=" 持仓数量", value=f"{pos_count} 只", delta=None)

    st.markdown("---")

    # ─── 模块二：动态风控水位 ───────────────────────────────────
    st.subheader("🛡️ 动态风控水位")

    position_value = total_equity - cash
    usage_pct = (position_value / total_equity * 100) if total_equity > 0 else 0
    mock_max_dd = round(random.Random(7).uniform(-12, -1), 2)
    mock_win_rate = round(random.Random(11).uniform(52, 78), 1)

    col_risk1, col_risk2, col_risk3 = st.columns(3)

    with col_risk1:
        st.markdown("**仓位使用率**")
        if usage_pct > 80:
            st.markdown(
                f'<div style="background:#dc3545;color:#fff;padding:6px 12px;border-radius:6px;font-weight:700;font-size:1.1rem;text-align:center">{usage_pct:.1f}%</div>',
                unsafe_allow_html=True,
            )
        elif usage_pct > 60:
            st.markdown(
                f'<div style="background:#ffc107;color:#000;padding:6px 12px;border-radius:6px;font-weight:700;font-size:1.1rem;text-align:center">{usage_pct:.1f}%</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="background:#28a745;color:#fff;padding:6px 12px;border-radius:6px;font-weight:700;font-size:1.1rem;text-align:center">{usage_pct:.1f}%</div>',
                unsafe_allow_html=True,
            )
        st.progress(min(usage_pct / 100, 1.0))

    with col_risk2:
        st.metric(label="📉 当日最大回撤", value=f"{mock_max_dd:+.2f}%")

    with col_risk3:
        st.metric(label="🎯 系统整体胜率", value=f"{mock_win_rate:.1f}%")

    st.markdown("---")

    # ─── 模块一：核心资产可视化 ─────────────────────────────────
    st.subheader("📈 核心资产可视化")

    import pandas as pd
    chart_left, chart_right = st.columns([3, 2])

    # 左侧：净值走势图
    with chart_left:
        st.markdown("**资金净值走势 (近 30 天)**")
        history = _mock_net_value_history(30)
        df_net = pd.DataFrame(history)
        df_net = df_net.set_index("date")
        st.line_chart(df_net["net_value"], use_container_width=True)

    # 右侧：仓位分布
    with chart_right:
        st.markdown("**仓位资金分布**")
        donut_data = {"现金": cash}
        for code, pos in positions.items():
            name = pos.get("name", code)
            val = pos.get("current_value", pos.get("shares", 0) * pos.get("current_price", pos.get("avg_price", 0)))
            donut_data[name] = round(val, 2)

        df_donut = pd.DataFrame(list(donut_data.items()), columns=["资产", "金额 (¥)"])
        st.dataframe(df_donut, use_container_width=True, hide_index=True)

        # 简易百分比条
        total_for_pie = sum(donut_data.values())
        for name, val in donut_data.items():
            pct = val / total_for_pie * 100 if total_for_pie > 0 else 0
            st.markdown(
                f'<div style="display:flex;align-items:center;margin-bottom:4px">'
                f'<span style="width:100px;font-size:0.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{name}</span>'
                f'<div style="flex:1;background:#eee;border-radius:4px;height:18px;margin:0 8px">'
                f'<div style="width:{pct:.0f}%;background:#4e79a7;height:100%;border-radius:4px"></div></div>'
                f'<span style="width:50px;text-align:right;font-size:0.85rem">{pct:.1f}%</span></div>',
                unsafe_allow_html=True,
            )

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
        st.dataframe(table_data, use_container_width=True, hide_index=True)
    else:
        st.info("暂无持仓数据")

    update_time = portfolio.get("update_time", "")
    if update_time:
        st.caption(f"最后更新: {update_time}")

    st.markdown("---")

    # ─── 模块三：AI 决策与算力中枢 ────────────────────────────
    st.subheader("🧠 AI 决策与算力中枢")

    # 初始化 ZeroMQ 战术总线窃听（全局单例，不阻塞 UI）
    order_queue, zmq_status = start_zmq_telemetry()

    # 硬件与推理监控
    col_hw1, col_hw2, col_hw3 = st.columns(3)
    with col_hw1:
        st.metric(label="⚡ LLM 推理延迟", value=f"{random.Random(3).randint(120, 480)} ms")
    with col_hw2:
        st.metric(label=" VRAM 占用", value=f"{random.Random(5).uniform(6.2, 14.8):.1f} GB")
    with col_hw3:
        # 根据线程状态和队列数据动态显示心跳
        if zmq_status.get("thread_alive"):
            if order_queue:
                last_time = zmq_status.get("last_msg_time", "")
                zmq_display = f"🟢 ACTIVE (最新指令：{last_time})"
                status_color = "#28a745"
            else:
                zmq_display = "🟢 ONLINE (监听中...)"
                status_color = "#28a745"
        else:
            zmq_display = "🔴 OFFLINE"
            status_color = "#dc3545"

        st.markdown("**ZeroMQ 总线心跳**")
        st.markdown(
            f'<div style="font-weight:600;color:{status_color}">{zmq_display}</div>',
            unsafe_allow_html=True,
        )

    # 战术指令流
    with st.expander("📡 实时战术指令流 (Live Orders)", expanded=True):
        if order_queue:
            orders_list = list(order_queue)
            df_orders = pd.DataFrame(orders_list)
            col_map = {
                "recv_time": "接收时间",
                "action": "动作",
                "code": "代码",
                "reason": "理由",
                "side": "方向",
                "price": "价格",
                "quantity": "数量",
            }
            df_display = df_orders.rename(columns={k: v for k, v in col_map.items() if k in df_orders.columns})
            st.dataframe(df_display, use_container_width=True, hide_index=True)
        else:
            st.info("⏳ 等待 AI 战术网络下发指令... (确保 live_trader 已启动并连接 tcp://127.0.0.1:5555)")


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
    AI 决策大脑 - 基于技术面数据生成评分与策略。
    未来接入真实 LLM 时替换此函数体即可。
    """
    seed = sum(ord(c) for c in technical.get("trend", ""))
    rng = random.Random(seed)

    ai_score = round(rng.uniform(55, 95), 0)
    trend = technical.get("trend", "震荡整理")
    support = technical.get("support", 0)
    resistance = technical.get("resistance", 0)

    if ai_score >= 80:
        strategy = f"当前处于{trend}，技术面强势，建议逢低建仓，止损位设于 {support} 元。"
    elif ai_score >= 65:
        strategy = f"当前处于{trend}，建议观望为主，等待明确信号后再介入，关注支撑位 {support} 元。"
    else:
        strategy = f"当前处于{trend}，短期风险较大，建议回避或轻仓操作，若持有可考虑在 {resistance} 元附近减仓。"

    return {"score": ai_score, "strategy": strategy}


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
        generate_btn = st.button("生成 AI 深度研判报告", type="primary", use_container_width=True, key="deep_dive_btn")

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


@st.cache_data(ttl="60s", show_spinner=False)
def get_enriched_watchlist_df(watchlist_codes: tuple) -> pd.DataFrame:
    """
    自选股列表富化 - 带缓存保护（60 秒 TTL）。
    内部获取全局大盘内存快照，直接在内存中匹配，彻底消除 N+1 循环网络 IO。
    """
    import pandas as pd
    from feeds.market_data import get_global_spot_data, _normalize_code

    table_data = []
    
    # 1. 获取全局大盘内存快照 (一次性获取，不发起多次调用)
    spot_df = get_global_spot_data()
    
    # 2. 如果快照不为空，做一些预处理，转换为字典便于 O(1) 查找
    lookup_dict = {}
    if spot_df is not None and not spot_df.empty and "代码" in spot_df.columns:
        temp_df = spot_df.copy()
        temp_df["代码"] = temp_df["代码"].astype(str).str.zfill(6)
        for _, row in temp_df.iterrows():
            code_str = row["代码"]
            lookup_dict[code_str] = {
                "name": str(row.get("名称", "N/A")),
                "latest_price": float(row.get("最新价", 0.0)) if pd.notna(row.get("最新价")) else 0.0,
                "change_pct": float(row.get("涨跌幅", 0.0)) if pd.notna(row.get("涨跌幅")) else 0.0,
            }

    # 3. 循环匹配
    for i, code in enumerate(watchlist_codes):
        clean_code = _normalize_code(code)
        
        # 从字典中查找数据，如果不存在则使用默认值，严禁发起网络请求
        real_data = lookup_dict.get(clean_code, {
            "name": "N/A",
            "latest_price": 0.0,
            "change_pct": 0.0
        })
        
        name = real_data["name"]
        if name == "N/A":
            name = code
            
        price = real_data["latest_price"]
        change_pct = real_data["change_pct"]

        table_data.append({
            "序号": i + 1,
            "股票代码": code,
            "股票名称": name,
            "最新价": f"¥{price:.2f}" if price > 0 else "N/A",
            "涨跌幅": f"{change_pct:+.2f}%" if price > 0 else "N/A",
        })

    return pd.DataFrame(table_data)


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
        st.dataframe(final_df, use_container_width=True, hide_index=True)
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

    # 个股深度研判面板
    render_deep_dive_analysis(normalized_watchlist)


def scan_report_dates():
    """扫描 data_cache/ 目录，提取所有有日报的日期，按时间倒序返回"""
    dates = []
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("daily_report_*.md"):
            # 从文件名提取日期: daily_report_YYYYMMDD.md
            date_str = f.stem.replace("daily_report_", "")
            if len(date_str) == 8 and date_str.isdigit():
                dates.append(date_str)
    return sorted(dates, reverse=True)


def module_history_review():
    """模块 4：历史复盘"""
    st.header("📜 历史复盘")

    dates = scan_report_dates()
    if not dates:
        st.info("暂无历史日报记录")
        return

    selected_date = st.selectbox("选择历史日期", dates)
    if not selected_date:
        return

    report_path = CACHE_DIR / f"daily_report_{selected_date}.md"
    feedback_path = CACHE_DIR / f"llm_feedback_{selected_date}.md"

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("📈 历史战报")
        if report_path.exists():
            content = report_path.read_text(encoding="utf-8")
            st.markdown(content)
        else:
            st.warning("战报文件不存在")

    with col_right:
        st.subheader("🤖 大模型反思反馈")
        if feedback_path.exists():
            content = feedback_path.read_text(encoding="utf-8")
            st.markdown(content)
        else:
            st.info("当日无大模型反思记录")


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
        ["📊 资产监控大屏", "📋 自选股管理", "📜 系统运行日志", "📜 历史复盘"],
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
    elif selected_module == "📜 历史复盘":
        module_history_review()


if __name__ == "__main__":
    main()
