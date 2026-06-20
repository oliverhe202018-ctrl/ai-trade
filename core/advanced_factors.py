"""
高级深度因子模块 - 负责拉取宏观舆情新闻与个股主力资金流向
带防封杀(连接池复用)机制
"""
import requests

from core.logger_config import logger

# 核心防封杀：建立全局 Session，复用底层 TCP 连接，伪装正常浏览器
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Connection": "keep-alive",
})


def get_macro_news():
    """
    千里眼：获取新浪财经 7x24 小时实时电报 (最新 5 条重磅)
    接口极其稳定，天然适合作为大模型的宏观背景输入
    """
    url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=5&page=1"
    try:
        resp = http_session.get(url, timeout=5)
        data = resp.json()
        news_list = [item["title"] for item in data.get("result", {}).get("data", [])]
        if news_list:
            return " | ".join(news_list)
        return "今日暂无重大宏观新闻。"
    except Exception as e:
        logger.exception(f"[WARN] 宏观新闻获取失败: {e}")
        return "宏观新闻获取失败，请专注于技术面与资金面。"


def get_main_fund_flow(secid):
    """
    主力追踪：获取个股今日主力净流入 (单位：万元)
    东方财富 f62 字段 (包含超大单和大单净流入)
    """
    url = f"http://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f62"
    try:
        resp = http_session.get(url, timeout=3)
        data = resp.json()
        net_inflow = data.get("data", {}).get("f62")

        if net_inflow and net_inflow != "-":
            # 将底层单位转化为"万元"，保留两小数
            return round(float(net_inflow) / 10000, 2)
        return 0.0
    except Exception:
        return 0.0
