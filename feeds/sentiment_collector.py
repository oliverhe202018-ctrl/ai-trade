"""
情绪嗅探器 (Sentiment Collector)
职责：利用 AkShare 获取真实世界的宏观情绪与资金面文本，转化为大模型的 Prompt 上下文。
"""
import akshare as ak
from datetime import datetime
from core.logger_config import logger

def get_morning_telegraph():
    """
    获取财联社今日电报（早盘宏观定调）

    ⚠️ AKShare 接口变更应急指南 ⚠️
    ─────────────────────────────────────────────────────────
    AKShare 更新极为频繁，电报类接口经常改名或废弃。
    如果本函数再次报错 "module 'akshare' has no attribute 'xxx'"，
    请按以下步骤排查并替换：

    1. 在 Python 交互环境中执行以下命令，搜索当前可用的电报/快讯接口：
         import akshare as ak
         [name for name in dir(ak) if 'telegraph' in name.lower() or 'cls' in name.lower() or 'news' in name.lower()]

    2. 从返回的列表中挑选最像"财联社电报"的接口名（通常含 telegraph / cls / news 关键词），
       调用一次看看返回的 DataFrame 结构：
         df = ak.候选接口名()
         print(df.columns)
         print(df.head())

    3. 将下方 ak.stock_zh_a_telegraph() 替换为新接口名，
       并根据实际列名调整 df.head(5)['内容'] 中的列名（可能是 '内容'/'title'/'text' 等）。

    4. 验证：直接运行本文件 python sentiment_collector.py 确认输出正常。
    ─────────────────────────────────────────────────────────
    """
    try:
        df = ak.stock_info_global_em()
        # 取最近的 5 条重磅电报（列名以实际返回为准）
        content_col = '标题' if '标题' in df.columns else ('内容' if '内容' in df.columns else df.columns[1])
        recent_news = df.head(5)[content_col].astype(str).tolist()
        return " | ".join(recent_news)
    except Exception as e:
        logger.error(f"电报获取失败: {e}")
        return "宏观情绪未知"

def get_market_temperature():
    """获取全市场涨跌停温度计"""
    try:
        df = ak.stock_zh_a_spot_em()
        up_count = len(df[df['涨跌幅'] > 0])
        down_count = len(df[df['涨跌幅'] < 0])
        limit_up = len(df[df['涨跌幅'] >= 9.8])
        limit_down = len(df[df['涨跌幅'] <= -9.8])
        
        regime = "极端贪婪" if limit_up > 80 else "恐慌冰点" if limit_down > 50 else "震荡分化"
        return f"当前上涨家数 {up_count}，下跌 {down_count}。涨停 {limit_up} 家，跌停 {limit_down} 家。市场情绪判定：{regime}。"
    except Exception as e:
        return "市场温度数据获取失败"

def build_ai_context_prompt():
    """拼装最终喂给大模型的异构数据上下文"""
    telegraph = get_morning_telegraph()
    temp = get_market_temperature()
    
    prompt = f"""
    [当前系统宏观环境注入]
    时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    市场温度: {temp}
    宏观动态: {telegraph}
    
    请结合上述情绪，对接下来的个股技术面数据进行降维打击式研判。如果环境为"恐慌冰点"，请直接将所有右侧追涨策略的评分扣减 20 分。
    """
    return prompt

if __name__ == "__main__":
    print(build_ai_context_prompt())