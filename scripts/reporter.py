"""
日终资产结算与自动化播报引擎 (Daily Reporter)
职责：在每天盘后解析资产变动、生成 Markdown 战报并通过 Hermes 推送。
"""
import os
import sys
import json
import subprocess
from datetime import datetime

# === 极客视野修正 2.0：精准挂载项目根目录 ===
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

# 适配现有的工程目录结构
CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data_cache')
PORTFOLIO_FILE = os.path.join(CACHE_DIR, 'live_portfolio.json')
REPORT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')

def _load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return None
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取资产文件失败: {e}")
        return None

def generate_daily_report():
    """生成极客版日终战报"""
    logger.info("开始生成日终资产战报...")
    portfolio = _load_portfolio()
    if not portfolio:
        logger.warning("未找到有效资产账本，无法生成战报。")
        return None

    now_str = datetime.now().strftime('%Y-%m-%d')
    cash = portfolio.get('cash', 0)
    positions = portfolio.get('positions', {})
    
    total_market_value = 0
    pos_lines = []
    
    for code, pos in positions.items():
        shares = pos.get('shares', 0)
        avg_price = pos.get('avg_price', 0)
        current_price = pos.get('highest_price', avg_price) # 近似最新价
        market_value = shares * current_price
        total_market_value += market_value
        
        profit_pct = (current_price - avg_price) / avg_price * 100 if avg_price > 0 else 0
        status_icon = "🟢" if profit_pct > 0 else "🔴" if profit_pct < 0 else "⚪"
        
        pos_lines.append(
            f"| {code} | {shares} | ¥{avg_price:.2f} | ¥{current_price:.2f} | {status_icon} {profit_pct:.2f}% |"
        )
        
    total_equity = cash + total_market_value
    
    # 组装 Markdown 报告
    report_md = f"""# 🤖 Shadow Oracle 量化双擎日终战报
**生成时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 📊 资产总览
* **账户总净值:** `¥{total_equity:.2f}`
* **可用现金:** `¥{cash:.2f}`
* **仓位市值:** `¥{total_market_value:.2f}`
* **当前仓位率:** `{(total_market_value/total_equity*100) if total_equity > 0 else 0:.2f}%`

## 🛡️ 持仓明细
| 标的代码 | 持股数量 | 成本均价 | 现价评估 | 浮动盈亏 |
| :--- | :--- | :--- | :--- | :--- |
"""
    if pos_lines:
        report_md += "\n".join(pos_lines)
    else:
        report_md += "| - | 空仓 | - | - | - |"

    report_md += "\n\n> *“风控是唯一的生存法则，暴利是纪律的副产品。”*"

    # 保存报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_filename = os.path.join(REPORT_DIR, f"report_{now_str}.md")
    
    with open(report_filename, 'w', encoding='utf-8') as f:
        f.write(report_md)
        
    logger.info(f"✅ 战报已成功生成: {report_filename}")
    return report_filename

def push_to_wechat(report_filepath):
    """使用 Hermes 官方的底层 CLI 工具跨进程推送战报"""
    try:
        # 组装官方推送命令: hermes send --to weixin --file <路径>
        cmd = ["hermes", "send", "--to", "weixin", "--file", report_filepath]
        logger.info(f"正在通过 Hermes CLI 投递战报: {cmd}")
        
        # 执行命令并捕获输出
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            logger.info("✅ 战报已成功通过 Hermes 投递至微信！")
        else:
            logger.error(f"❌ 微信推送失败，底层报错: {result.stderr}")
    except Exception as e:
        logger.error(f"❌ 进程通信抛出异常: {e}")

def execute_daily_report_and_push():
    """供外部调用的总干道"""
    report_filepath = generate_daily_report() 
    if report_filepath:
        push_to_wechat(report_filepath)

# === 将点火器移至文件绝对底部，并直接调用完整闭环 ===
if __name__ == "__main__":
    execute_daily_report_and_push()