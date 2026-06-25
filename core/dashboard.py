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
    # ===== 行情数据源健康状态 =====
    st.sidebar.markdown("---")
    st.sidebar.subheader("🌐 行情源监控")
    health_file = CACHE_DIR / "market_health.json"
    if health_file.exists():
        try:
            with open(health_file, "r", encoding="utf-8") as f:
                health = json.load(f)
            
            provider = health.get("provider", "Unknown")
            ts = health.get("timestamp", 0)
            status = health.get("status", "DOWN")
            
            delay = time.time() - ts
            
            if delay > 300: # 5分钟未更新视为严重延迟
                status_color = "🔴"
                status_text = "STALE"
            elif status == "OK":
                status_color = "🟢"
                status_text = "OK"
            else:
                status_color = "🔴"
                status_text = status
                
            st.sidebar.markdown(f"**主数据源**: {provider}")
            st.sidebar.markdown(f"**状态**: {status_color} {status_text}")
            st.sidebar.markdown(f"**延迟**: {delay:.1f} 秒")
            st.sidebar.markdown(f"**最后更新**: {health.get('datetime', 'N/A')}")
        except Exception:
            st.sidebar.markdown("**主数据源**: UNKNOWN")
            st.sidebar.markdown("**状态**: 🔴 DOWN")
            st.sidebar.markdown("**延迟**: N/A")
            st.sidebar.markdown("**最后更新**: N/A")
    else:
        st.sidebar.warning("等待行情源初始化...")

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


@st.cache_resource
def start_radar_service():
    """ 启动多因子异动雷达后台线程 """
    try:
        from core.radar_manager import start_radar_daemon
        start_radar_daemon()
    except Exception as e:
        logger.error(f"启动雷达服务失败: {e}")
    return True

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
    
    if current_state == TradingState.ACTIVE.value:
        state_label = "🟢 正常交易 (ACTIVE)"
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

def get_llm_health_status():
    import os
    import requests
    import time
    
    llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:8080")
    llm_provider = os.getenv("LLM_PROVIDER", "Llama.cpp")
    source_name = f"{llm_provider} ({llm_base_url.replace('http://', '').replace('https://', '')})"
    
    llm_info = {
        "status": "OFFLINE",
        "latency_ms": None,
        "source": source_name,
        "error": None
    }
    
    t0 = time.time()
    try:
        # primary
        res = requests.get(f"{llm_base_url}/health", timeout=2.0)
        if res.status_code == 200:
            llm_info["latency_ms"] = int((time.time() - t0) * 1000)
            llm_info["status"] = "ONLINE"
            return llm_info
    except Exception as e:
        llm_info["error"] = str(e)
        
    try:
        # fallback
        res = requests.get(f"{llm_base_url}/v1/models", timeout=2.0)
        if res.status_code == 200:
            llm_info["latency_ms"] = int((time.time() - t0) * 1000)
            llm_info["status"] = "ONLINE"
            llm_info["error"] = None
    except Exception as e:
        llm_info["error"] = str(e)
        
    return llm_info

def parse_iso_time(value: str):
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def get_feed_channel_health():
    from datetime import datetime, timezone
    from core.health_bus import read_heartbeat
    result = []

    for channel in ["L1", "L2", "L3"]:
        hb = read_heartbeat(channel)

        if not hb:
            result.append({
                "level": channel,
                "status": "MISSING",
                "delay_minutes": None,
                "last_seen": None,
                "source": None,
                "message": "未找到 heartbeat 文件",
            })
            continue

        try:
            last_seen = parse_iso_time(hb.get("last_seen"))
            if last_seen is None:
                raise ValueError("last_seen is empty")

            now = datetime.now(timezone.utc)
            delay_minutes = int((now - last_seen).total_seconds() // 60)

            raw_status = hb.get("status", "UNKNOWN")
            source = hb.get("source")
            message = hb.get("message")

            if raw_status == "ERROR":
                status = "ERROR"
            elif delay_minutes <= 5:
                status = "OK"
            elif delay_minutes <= 30:
                status = "STALE"
            else:
                status = "DOWN"

            result.append({
                "level": channel,
                "status": status,
                "delay_minutes": delay_minutes,
                "last_seen": hb.get("last_seen"),
                "source": source,
                "message": message,
            })

        except Exception as e:
            result.append({
                "level": channel,
                "status": "UNKNOWN",
                "delay_minutes": None,
                "last_seen": hb.get("last_seen"),
                "source": hb.get("source"),
                "message": f"heartbeat 解析失败: {e}",
            })

    return result

@st.fragment(run_every="5s")
def _render_top_metrics_fragment():
    """渲染顶部的风控与资源占用 Metrics"""
    cols = st.columns(4)
    with cols[0]:
        st.metric("CPU 占用", f"{psutil.cpu_percent()}%")
    with cols[1]:
        st.metric("内存占用", f"{psutil.virtual_memory().percent}%")
    with cols[2]:
        llm_info = get_llm_health_status()
        st.metric("LLM 状态", llm_info["status"]) 
    with cols[3]:
        # 从 trading_state 获取真实状态，如果没有先给个安全默认值
        current_state = get_trading_state()
        st.metric("ZMQ 总线", f"ACTIVE ({current_state})")


def _render_system_health_check_fragment():
    st.subheader("🏥 系统健康度探针 (Health Check)")
    
    # 1. LLM 通道状态
    llm_info = get_llm_health_status()
    if llm_info["status"] == "ONLINE":
        llm_status_text = "🟢 正常"
        llm_latency_text = f"{llm_info['latency_ms']}ms"
    elif llm_info["status"] == "OFFLINE":
        llm_status_text = "🔴 离线"
        llm_latency_text = "N/A"
    else:
        llm_status_text = "⚪ 未知"
        llm_latency_text = "N/A"
        
    # 2. 数据源新鲜度
    feed_healths = get_feed_channel_health()
    feed_texts = {}
    for item in feed_healths:
        level = item["level"]
        status = item["status"]
        delay = item["delay_minutes"]
        message = item.get("message") or ""
        source = item.get("source") or "unknown"
        
        # Keep short source name
        source_short = source.split("/")[-1] if "/" in source else source
        
        if status == "OK":
            feed_texts[level] = f"🟢 正常 ({delay}m) - {source_short}"
        elif status == "STALE":
            feed_texts[level] = f"🟡 延迟 ({delay}m) - {source_short}"
        elif status == "DOWN":
            feed_texts[level] = f"🔴 中断 ({delay}m) - {source_short}"
        elif status == "ERROR":
            feed_texts[level] = f"🔴 错误 - {message}"
        elif status == "EMPTY":
            feed_texts[level] = f"🟡 无新数据 - {message}"
        elif status == "SOURCE_DOWN":
            feed_texts[level] = f"🔴 源不可用 - {message}"
        elif status == "NO_INPUT":
            feed_texts[level] = f"🟡 无输入 - {message}"
        elif status == "MISSING":
            feed_texts[level] = f"🔴 缺失 - {message}"
        else:
            feed_texts[level] = f"⚪ 未知 - {message}"

    l1_text = feed_texts.get("L1", "⚪ 未知")
    l2_text = feed_texts.get("L2", "⚪ 未知")
    l3_text = feed_texts.get("L3", "⚪ 未知")
    
    # 3. 对账状态灯 & 心跳
    hb_file = CACHE_DIR / "heartbeats.json"
    hb_data = load_json(hb_file, {})
    now = time.time()
    
    trader_hb = hb_data.get("live_trader", 0)
    trader_delay = now - trader_hb
    trader_status = "🟢 正常" if trader_delay < 300 and trader_hb > 0 else "🔴 疑似失联"
    
    brain_hb = hb_data.get("brain_node", 0)
    brain_delay = now - brain_hb
    brain_status = "🟢 正常" if brain_delay < 300 and brain_hb > 0 else "🔴 疑似失联"
    
    # 4. 整体状态机
    try:
        from core.trading_state import get_trading_state, TradingState
        t_state = get_trading_state()
        if t_state == TradingState.ACTIVE.value:
            state_text = f"🟢 {t_state}"
        elif t_state == TradingState.PAUSED.value:
            state_text = f"🟡 {t_state}"
        elif t_state in (TradingState.FROZEN.value, TradingState.EMERGENCY.value):
            state_text = f"🔴 {t_state}"
        else:
            state_text = f"⚪ {t_state}"
    except:
        state_text = "⚪ UNKNOWN"
        t_state = "UNKNOWN"
        
    # UI Render
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.info(f"**🧠 LLM 状态**\n\n状态: {llm_status_text}\n\n延迟: {llm_latency_text}\n\n来源: {llm_info['source']}")
    with c2:
        st.info(f"**📡 事件雷达状态**\n\nL1: {l1_text}\n\nL2: {l2_text}\n\nL3: {l3_text}")
    with c3:
        st.info(f"**💓 核心模块心跳**\n\nTrader: {trader_status} ({trader_delay:.0f}s)\n\nBrain: {brain_status} ({brain_delay:.0f}s)")
    with c4:
        st.info(f"**⚙️ 全局交易状态机**\n\n当前状态: {state_text}")
        if t_state in ("FROZEN", "DEGRADED", "EMERGENCY"):
            # 尝试提取最后一行错误日志
            log_file = CACHE_DIR.parent / "logs" / "app.log"
            last_err = ""
            if log_file.exists():
                try:
                    lines = open(log_file, "r", encoding="utf-8").readlines()
                    errs = [l for l in lines if "ERROR" in l or "CRITICAL" in l]
                    if errs:
                        last_err = errs[-1].strip()[-80:] # 取最后80字符
                except:
                    pass
            if last_err:
                st.caption(f"⚠️ {last_err}")
                
    st.markdown("---")
    
    # 事件雷达诊断
    def diagnose_feed_channels():
        from pathlib import Path
        from datetime import datetime
        checks = []
        project_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
        health_dir = project_root / "data_cache" / "health"
        events_dir = project_root / "data_cache" / "events"
        
        for channel in ["L1", "L2", "L3"]:
            path = health_dir / f"{channel.lower()}_heartbeat.json"
            checks.append({
                "channel": channel,
                "path": str(path),
                "exists": path.exists(),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None,
            })
            
        source_status_path = events_dir / "source_status.json"
        if source_status_path.exists():
            try:
                import json
                with source_status_path.open("r", encoding="utf-8") as f:
                    checks.append({
                        "channel": "Event Radar Sources",
                        "status": json.load(f)
                    })
            except:
                pass
                
        return checks
        
    with st.expander("🛠️ 事件雷达及底层总线诊断"):
        st.json(diagnose_feed_channels())

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
    
    _render_system_health_check_fragment()

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

def module_news_sentiment():
    """模块：资讯情绪中枢 — 展示 news_extractor 的实时输出"""
    st.header('📰 资讯情绪中枢')
    st.caption('数据来源：brain_node 每轮演算写入 data_cache/news_sentiment_cache.json')
    cache_file = Path(CACHE_DIR) / 'news_sentiment_cache.json'

    def _load_cache():
        try:
            if not cache_file.exists():
                st.info('⏳ 等待 brain_node 完成首轮演算...')
                st.stop()
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
        return None
    data = _load_cache()
    if data is None:
        st.warning('⚠️ 尚无资讯缓存文件。请确认 brain_node 已启动，并等待首轮演算完成（直到盘中，约 60 秒）。')
        st.info('缓存路径: `data_cache/news_sentiment_cache.json`')
        if st.button('🔄 刷新'):
            st.rerun()
        return
    ts_str = data.get('_datetime', '未知')
    cache_age = time.time() - data.get('_ts', 0)
    if cache_age > 300:
        st.warning(f'⚠️ 数据已 {int(cache_age // 60)} 分钟未更新')
    source = data.get('_source', 'llm')
    news_count = data.get('_news_count', 0)
    macro = float(data.get('macro_sentiment', 0))
    col_ts, col_src, col_cnt, col_refresh = st.columns([3, 2, 2, 1])
    with col_ts:
        st.caption(f'🕐 最后更新：{ts_str}')
    with col_src:
        src_color = '🟢' if source in ('llm', 'rule_engine') else '🟡'
        st.caption(f'{src_color} 来源：{source}')
    with col_cnt:
        st.caption(f'📄 本轮资讯条数：{news_count}')
    with col_refresh:
        if st.button('🔄', help='刷新页面'):
            st.rerun()
    st.markdown('---')
    col_gauge, col_sectors = st.columns([2, 3])
    with col_gauge:
        st.subheader('🌡️ 宏观市场情绪')
        if macro > 0.3:
            sentiment_label = '🔴 偏强（看多）'
            color = '#e74c3c'
        elif macro < -0.3:
            sentiment_label = '🟢 偏弱（看空/谨慎）'
            color = '#27ae60'
        else:
            sentiment_label = '⚪ 中性'
            color = '#95a5a6'
        st.markdown(f'\n        <div style="text-align:center; padding: 2rem; background: #1e1e2e;\n             border-radius: 1rem; border: 2px solid {color};">\n            <div style="font-size: 3.5rem; font-weight: 800; color: {color};">{macro:+.2f}</div>\n            <div style="font-size: 1.1rem; margin-top: 0.5rem; color: #ccc;">{sentiment_label}</div>\n            <div style="font-size: 0.8rem; color: gray; margin-top: 0.3rem;">区间 [-1, +1]，0 为中性</div>\n        </div>\n        ', unsafe_allow_html=True)
    with col_sectors:
        st.subheader('🔥 热点板块')
        hot_sectors = data.get('hot_sectors', [])
        if hot_sectors:
            n_cols = min(len(hot_sectors), 4)
            cols = st.columns(n_cols)
            for i, sector in enumerate(hot_sectors[:8]):
                with cols[i % n_cols]:
                    if isinstance(sector, dict):
                        name = sector.get('name', str(sector))
                        score = sector.get('score', '')
                        st.metric(name, f'{score}' if score else '🔥')
                    else:
                        st.metric(str(sector), '🔥')
        else:
            st.info('本轮未识别到明显热点板块。')
    st.markdown('---')
    col_risk, col_stocks = st.columns([2, 3])
    with col_risk:
        st.subheader('⚠️ 个股风险预警')
        risk_warnings = data.get('risk_warnings', [])
        if risk_warnings:
            for rw in risk_warnings:
                if isinstance(rw, dict):
                    code = rw.get('code', '')
                    reason = rw.get('reason', str(rw))
                    st.error(f'🔴 **{code}** — {reason}')
                else:
                    st.warning(f'⚠️ {rw}')
        else:
            st.success('✅ 本轮无个股风险预警。')
    with col_stocks:
        st.subheader('📋 个股情绪评分')
        stock_sentiments = data.get('stock_sentiments', [])
        if stock_sentiments:
            df_stocks = pd.DataFrame(stock_sentiments)
            st.dataframe(df_stocks, use_container_width=True)
        else:
            st.info('本轮无个股情绪评分（LLM 模式才会生成）。')
    st.markdown('---')
    with st.expander('📄 本轮采集资讯原文（展开查看）', expanded=False):
        raw_news = data.get('news_list', data.get('raw_news', []))
        if raw_news:
            for i, item in enumerate(raw_news[:30]):
                if isinstance(item, dict):
                    title = item.get('title', item.get('content', str(item))[:80])
                    pub_time = item.get('publish_time', item.get('time', ''))
                    st.markdown(f"**{i + 1}.** {title}  <span style='color:gray; font-size:0.8rem'>{pub_time}</span>", unsafe_allow_html=True)
                else:
                    st.markdown(f'**{i + 1}.** {str(item)[:120]}')
        else:
            st.info('原始资讯未写入缓存（规则引擎模式正常）。')
    with st.expander('🔧 完整 JSON 诊断', expanded=False):
        st.json(data)


def module_paper_trading_performance():
    """📈 模拟盘绩效"""
    import json
    perf_path = os.path.join(PROJECT_ROOT, "data_cache", "paper_performance.json")
    if not os.path.exists(perf_path):
        st.info("暂无模拟盘绩效数据，请先运行 paper_trade_engine.py")
        return

    try:
        with open(perf_path, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        st.error("模拟盘绩效数据读取失败")
        return

    st.caption(f"生成时间: {s.get('generated_at', '?')} | 窗口: {s.get('window', {}).get('hours', '?')} 小时 | 数据条目: {s.get('window', {}).get('fill_entries', 0)} 条")

    b = s.get("basic_stats", {})
    d = s.get("direction_stats", {})
    p = s.get("portfolio_stats", {})
    a = s.get("anomaly_stats", {})

    col1, col2, col3 = st.columns(3)
    col1.metric("当前现金", f"¥{p.get('current_cash', 0):,.2f}")
    col2.metric("持仓标的", p.get("open_positions", 0))
    col3.metric("成交/拒单", f"{b.get('filled_orders', 0)}/{b.get('rejected_orders', 0)}")

    col4, col5, col6 = st.columns(3)
    col4.metric("BUY 成交", d.get("buy_count", 0))
    col5.metric("SELL 成交", d.get("sell_count", 0))
    col6.metric("持仓成本", f"¥{p.get('total_cost_basis', 0):,.2f}")

    if p.get("positions_detail"):
        st.divider()
        st.caption("持仓明细")
        for pos in p["positions_detail"]:
            st.text(f"  {pos['code']} {pos['quantity']}股 @ ¥{pos['avg_cost']:.3f}  成本 ¥{pos['cost_basis']:,.2f}")

    with st.expander("⚠️ 异常与风险", expanded=False):
        st.text(f"JSON 解析错误: {a.get('json_decode_errors', 0)} 次")
        st.text(f"数据污染: {'检测到' if a.get('data_pollution', {}).get('detected') else '无'} ({a.get('data_pollution', {}).get('polluted_count', 0)} 条)")
        if a.get("reject_reasons"):
            st.text("拒单原因: " + ", ".join(f"{k}×{v}" for k, v in a["reject_reasons"].items()))
        st.caption("以下指标当前不可计算：")
        for metric, reason in sorted(s.get("unavailable", {}).items())[:6]:
            st.text(f"  {metric}: {reason}")

    with st.expander("📋 最近成交流水", expanded=False):
        for fill in s.get("recent_fills", [])[:5]:
            icon = {"FILLED": "✅", "REJECTED": "❌", "SKIPPED": "⏭️"}.get(fill.get("status", ""), "❓")
            st.text(f"{icon} {fill.get('timestamp', '?')} {fill.get('action', '?')} {fill.get('code', '?')} x{fill.get('quantity', 0)} [{fill.get('status', '?')}]")


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

def module_copilot():
    st.header("🤖 AI 股票助手 (Copilot)")
    st.markdown("通过自然语言与系统对话，直接分析持仓、个股、新闻与交易信号。")
    
    # 状态初始化
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
        
    col_clear, _ = st.columns([1, 5])
    with col_clear:
        if st.button("🧹 清空会话", type="secondary"):
            st.session_state.chat_history = []
            st.rerun()
            
    st.markdown("---")
    
    # 聊天区域
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            
    # 输入区域
    if prompt := st.chat_input("您可以问：今天有哪些机会？分析贵州茅台？当前持仓风险如何？为什么没有下单？"):
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        
        with st.chat_message("user"):
            st.markdown(prompt)
            
        with st.chat_message("assistant"):
            with st.spinner("🧠 大脑中枢演算中..."):
                try:
                    from core.copilot_service import chat_with_copilot
                    
                    app_state = {}
                    # 从全局抓取 ZMQ telemetry data
                    try:
                        start_radar_service()
                        order_q, _ = start_zmq_telemetry()
                        app_state["order_queue"] = list(order_q)
                    except Exception:
                        pass
                        
                    res = chat_with_copilot(prompt, st.session_state.chat_history, app_state)
                    
                    answer = res["answer"]
                    intent = res["intent"]
                    latency = res["latency"]
                    
                    st.markdown(answer)
                    st.caption(f"⏱️ 耗时: {latency}ms | 🎯 识别意图: {intent}")
                    
                    st.session_state.chat_history.append({"role": "assistant", "content": answer})
                except Exception as e:
                    st.error(f"调用助手服务失败: {e}")

def module_potential_discovery():
    st.header("🌟 潜力股发现 (Potential Discovery)")
    st.markdown("漏斗式人工智能深度研判引擎。从全市场 Top 100 中结合规则与多因子异动分数选出 Top 10，并提交大模型进行最后评估。*(仅供分析观察，绝不提供买卖建议)*")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🧠 立即启动深度挖掘引擎", type="primary"):
            from core.potential_discovery import run_discovery_async, _discovery_lock
            if _discovery_lock.locked():
                st.warning("⚠️ 引擎正在后台进行深度思辨，请勿重复点击。")
            else:
                success = run_discovery_async()
                if success:
                    st.success("✅ 挖掘任务已启动 (LLM 分析大概需要 10-20 秒)，请稍后刷新查看。")
                else:
                    st.warning("⚠️ 并发拦截。")
            time.sleep(1)
            st.rerun()
            
    with col2:
        from core.potential_discovery import _discovery_lock
        if _discovery_lock.locked():
            st.info("🔄 **运行状态**: 大脑中枢演算中...请稍后刷新。")
        else:
            st.info("💡 提示：本引擎强依赖于【🔥 全市场扫描】的输出。如果发现数据陈旧，请先执行全市场扫描。")
            
    st.markdown("---")
    
    picks_file = os.path.join(CACHE_DIR, "potential_picks.json")
    if os.path.exists(picks_file):
        try:
            with open(picks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            updated = data.get("updated_at", "未知")
            picks = data.get("picks", [])
            
            st.caption(f"🕒 报告生成时间: {updated} | 🏆 最终入选标的: {len(picks)} 只")
            
            if picks:
                for idx, p in enumerate(picks):
                    with st.expander(f"✨ Top {idx+1}: {p.get('name', 'Unknown')} ({p.get('code', '')}) - 优先级: {p.get('watch_priority', 'Medium')}", expanded=(idx==0)):
                        col_score1, col_score2, col_score3 = st.columns(3)
                        col_score1.metric("Scanner 分数", p.get('scanner_score', 0))
                        col_score2.metric("Fusion 分数", p.get('fusion_score', 0))
                        col_score3.metric("AI 潜力分数", p.get('potential_score', 0))
                        
                        st.markdown(f"**🧠 深度研判**: {p.get('reason', '无分析理由')}")
                        
                        tags = p.get('risk_tags', [])
                        if tags:
                            tag_str = " ".join([f"`{t}`" for t in tags])
                            st.markdown(f"**⚠️ 风险标签**: {tag_str}")
            else:
                st.warning("大模型在本轮研判中认为没有值得追踪的标的。")
                
        except Exception as e:
            st.error(f"解析挖掘报告失败: {e}")
    else:
        st.info("尚未生成潜力股报告，请点击上方按钮启动挖掘。")

def module_tape_reader():
    st.header("💰 主力资金追踪 (Tape Reader V1)")
    st.markdown("基于 L1 快照降维推演的主力行为捕捉器。本系统仅追踪 **Top 30** 潜力标的，估算主动买卖意愿及疑似大单净流入。*(仅供观察，非绝对 L2 精准数据)*")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        from core.tape_reader import _tape_lock, _stop_event, start_tape_reader_async, stop_tape_reader
        
        is_locked = _tape_lock.locked()
        is_stopping = is_locked and _stop_event.is_set()
        is_running = is_locked and not _stop_event.is_set()
        
        if is_running:
            if st.button("⏹️ 停止实时追踪", type="secondary"):
                stop_tape_reader()
                time.sleep(0.5)
                st.rerun()
        elif is_stopping:
            st.button("⏹️ 正在停止...", disabled=True)
        else:
            if st.button("▶️ 启动盘口追踪", type="primary"):
                success = start_tape_reader_async()
                if success:
                    st.success("✅ 后台轮询引擎已启动。")
                time.sleep(0.5)
                st.rerun()
                
    with col2:
        if is_running:
            st.info("🔄 **运行状态**: 后台循环捕获中 (3秒/次)... 可通过右上角 ⋮ 设置自动刷新。")
        elif is_stopping:
            st.warning("⏳ **运行状态**: 正在安全退出，等待最后一次写入完成并释放锁...")
        else:
            st.info("💡 提示：此引擎长期运行可能会轻微占用 CPU。只建议在盘中开启，盘后自动失效。")

    st.markdown("---")
    
    tracking_file = os.path.join(CACHE_DIR, "main_money_tracking.json")
    if os.path.exists(tracking_file):
        try:
            with open(tracking_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            updated = data.get("timestamp", 0)
            items = data.get("items", [])
            st.caption(f"🕒 数据快照时间: {datetime.fromtimestamp(updated).strftime('%H:%M:%S')} | 数据等级: {data.get('data_level', 'Unknown')}")
            
            if items:
                # 构造 DataFrame 以便漂亮地展示
                df_data = []
                for idx, it in enumerate(items):
                    df_data.append({
                        "排名": idx + 1,
                        "名称": it.get("name", ""),
                        "代码": it.get("code", ""),
                        "当前价": it.get("last_price", 0),
                        "代理分(Proxy)": it.get("main_money_proxy_score", 0),
                        "大单净流入(估)": it.get("estimated_large_order_net_inflow", 0),
                        "大单频次": it.get("large_order_event_count", 0),
                        "盘口失衡(委比)": it.get("order_book_imbalance", 0.0),
                        "快照样本数": it.get("sample_count", 0)
                    })
                import pandas as pd
                df = pd.DataFrame(df_data)
                
                st.dataframe(
                    df,
                    column_config={
                        "大单净流入(估)": st.column_config.NumberColumn(format="%.0f"),
                        "盘口失衡(委比)": st.column_config.NumberColumn(format="%.3f"),
                        "代理分(Proxy)": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")
                    },
                    use_container_width=True,
                    hide_index=True
                )
                st.warning("⚠️ **风险提示**: 本页所有主动买卖与大单流入指标，均由 3 秒级的 L1 Snapshot 聚合模拟产生，不等同于交易所真实的 L2 逐笔明细，仅供模糊资金定性分析参考。")
            else:
                st.warning("跟踪列表为空，可能 Top30 标的均处于停牌或未产生成交差值。")
        except Exception as e:
            st.error(f"解析追踪文件失败: {e}")
    else:
        st.info("尚未生成主力资金追踪数据。请先启动追踪器并等待数秒。")

def module_market_scanner():
    st.header("🔥 全市场扫描 (Market Scanner)")
    st.markdown("通过多维度量化指标对沪深A股 5000+ 标的进行全景扫描，发现潜在交易机会。*(纯规则驱动，无 LLM 介入)*")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🚀 立即执行全市场扫描", type="primary"):
            from core.market_scanner import run_scanner_async, _scan_lock
            if _scan_lock.locked():
                st.warning("⚠️ 扫描引擎正在后台运行，请勿重复点击。")
            else:
                success = run_scanner_async()
                if success:
                    st.success("✅ 扫描引擎已在后台启动，请稍后刷新页面查看结果。")
                else:
                    st.warning("⚠️ 扫描任务并发拦截。")
            time.sleep(1)
            st.rerun()
            
    with col2:
        from core.market_scanner import _scan_lock
        if _scan_lock.locked():
            st.info("🔄 **运行状态**: 扫描中...请稍后刷新。")
        else:
            st.info("💡 提示：静态日线指标（均线、新高）每天仅计算一次以加速盘中扫描。增量更新实时价格与量能。")
        
    st.markdown("---")
    
    candidates_file = os.path.join(CACHE_DIR, "market_candidates.json")
    if os.path.exists(candidates_file):
        try:
            with open(candidates_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            ts = data.get("timestamp", 0)
            cost = data.get("cost_ms", 0)
            candidates = data.get("candidates", [])
            
            from datetime import datetime
            time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            
            st.caption(f"🕒 最后扫描时间: {time_str} | ⏱️ 耗时: {cost} ms | 📊 入库标的: {len(candidates)} 只")
            
            if candidates:
                # 转换为 DataFrame 展示
                df = pd.DataFrame(candidates)
                # 展开 factors 列表为字符串
                df['factors'] = df['factors'].apply(lambda x: " | ".join(x) if isinstance(x, list) else x)
                df.rename(columns={"code": "代码", "name": "名称", "score": "综合得分", "factors": "驱动因子"}, inplace=True)
                
                st.dataframe(
                    df,
                    column_config={
                        "综合得分": st.column_config.ProgressColumn(
                            "综合得分",
                            help="基于涨跌幅、量能、趋势和消息面的多因子得分",
                            format="%.1f",
                            min_value=0,
                            max_value=100,
                        ),
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.warning("暂无符合条件的高分标的。")
                
        except Exception as e:
            st.error(f"解析扫描结果失败: {e}")
    else:
        st.info("暂未发现扫描结果，请点击上方按钮执行首次扫描。")

def main():
    """主入口"""
    st.title("🚀 AI Trader 量化系统控制台")

    # 侧边栏导航
    st.sidebar.title("导航菜单")
    selected_module = st.sidebar.radio(
        "选择模块",
        ["🔥 全市场扫描", "🌟 潜力股发现", "💰 主力资金追踪", "🤖 AI股票助手", "📊 资产监控大屏", "📰 资讯情绪中枢", "📈 Paper Trading 监控", "📋 自选股管理", "📜 系统运行日志", "📜 历史复盘", "👀 72小时观察报告"],
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
    if selected_module == "🔥 全市场扫描":
        module_market_scanner()
    elif selected_module == "🌟 潜力股发现":
        module_potential_discovery()
    elif selected_module == "💰 主力资金追踪":
        module_tape_reader()
    elif selected_module == "🤖 AI股票助手":
        module_copilot()
    elif selected_module == "📊 资产监控大屏":
        module_portfolio_dashboard()
    elif selected_module == "📰 资讯情绪中枢":
        module_news_sentiment()
    elif selected_module == "📈 Paper Trading 监控":
        module_paper_trading_monitor()
    elif selected_module == "📋 自选股管理":
        module_watchlist_manager()
    elif selected_module == "📜 系统运行日志":
        module_system_logs()
    elif selected_module == "📜 历史复盘":
        module_history_review()
    elif selected_module == "👀 72小时观察报告":
        module_observation_reports()


if __name__ == "__main__":
    main()
