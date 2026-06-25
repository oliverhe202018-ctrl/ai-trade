import re

# 1. Update live_trader.py
content = open('live_trader.py', encoding='utf-8').read()

old_twap_check = '''            if cash > 0 and order_amount > cash * 0.1 and order.get('quantity', 0) >= 300:
                if market_provider:
                    try:
                        ob = market_provider.get_orderbook(order['code'])'''

new_twap_check = '''            if cash > 0 and order_amount > cash * 0.1 and order.get('quantity', 0) >= 300:
                if market_provider:
                    try:
                        tick = market_provider.get_realtime_quote(order['code'])
                        vol = tick.get('volume')
                        amt = tick.get('amount')
                        if vol is None or amt is None or vol == 0 or amt == 0:
                            logger.warning(f"[TWAP_CANCEL] {order['code']} 缺少成交量或成交额字段，无流动性，取消大额拆单！")
                            continue
                            
                        ob = market_provider.get_orderbook(order['code'])
                        if ob is None:
                            logger.warning(f"[TWAP_WARN] {order['code']} 缺少五档盘口，返回None。")
                            ob = {}'''

content = content.replace(old_twap_check, new_twap_check)
open('live_trader.py', 'w', encoding='utf-8').write(content)

# 2. Update brain_node.py
content = open('brain_node.py', encoding='utf-8').read()

old_atr_check = '''                try:
                    atr = calculate_atr(_stock_history.get(code))
                    position_size = calculate_position_size('''

new_atr_check = '''                try:
                    hist_data = _stock_history.get(code)
                    if hist_data is None or len(hist_data) < 20:
                        logger.warning(f"[K线不足] {code} 历史数据不足20根，无法计算真实 ATR 与指标，默认跳过，返回 NO_TRADE。")
                        continue
                        
                    atr = calculate_atr(hist_data)
                    position_size = calculate_position_size('''

content = content.replace(old_atr_check, new_atr_check)
open('brain_node.py', 'w', encoding='utf-8').write(content)
