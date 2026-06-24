from xtquant import xttrader
from xtquant.xttype import StockAccount
import random

path = r'D:\国金证券QMT交易端\userdata_mini'
account_id = '8881541669'
session_id = int(random.randint(100000, 999999))

xt_trader = xttrader.XtQuantTrader(path, session_id)
acc = StockAccount(account_id)

xt_trader.start()
connect_result = xt_trader.connect()
if connect_result == 0:
    print("QMT_CONNECT_SUCCESS")
    xt_trader.subscribe(acc)
    asset = xt_trader.query_stock_asset(acc)
    if asset:
        print(f"TOTAL_ASSET: {getattr(asset, 'total_asset', 'N/A')}")
        print(f"CASH: {getattr(asset, 'cash', 'N/A')}")
else:
    raise ConnectionError(f"QMT_CONNECT_FAILED with code: {connect_result}")
