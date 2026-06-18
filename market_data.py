"""
实时行情数据源 - 全市场三级漏斗扫描 (防封杀版)
第一级：东方财富成交额排行抓取
第二级：ST/退市/一字涨跌停规则清洗
第三级：并发计算 MACD/MA5 技术因子 + 主力资金流向
失败降级：本地 WATCHLIST 模拟数据兜底
"""
import os
import json
import sys
import time
import random
import pandas as pd
import concurrent.futures
from datetime import datetime
from functools import lru_cache

# 防封杀：复用 advanced_factors 的全局 Session
from advanced_factors import get_main_fund_flow, http_session

# 本地文件缓存目录
CACHE_DIR = "./data_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# 自定义自选股配置文件路径
CUSTOM_WATCHLIST_FILE = os.path.join(CACHE_DIR, "custom_watchlist.json")


def _load_custom_watchlist():
    """
    加载自定义自选股白名单。
    若文件不存在则自动创建空列表。
    支持两种格式：
      - 旧格式: ["sh600519", "sz000001"]
      - 新格式: [{"code": "sh600519", "strategy": "auto", "notes": "中线底仓"}]
    返回: (codes_list, strategy_map)
      codes_list: 股票代码列表 ["sh600519", ...]
      strategy_map: {code: strategy} 非 auto 策略的路由映射
    """
    if not os.path.exists(CUSTOM_WATCHLIST_FILE):
        try:
            with open(CUSTOM_WATCHLIST_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
            sys.stderr.write(f"[INFO] 已创建空自选股配置文件: {CUSTOM_WATCHLIST_FILE}\n")
            return [], {}
        except Exception as e:
            sys.stderr.write(f"[WARN] 创建自选股配置文件失败: {e}\n")
            return [], {}

    try:
        with open(CUSTOM_WATCHLIST_FILE, "r", encoding="utf-8") as f:
            watchlist = json.load(f)
            if not isinstance(watchlist, list):
                sys.stderr.write("[WARN] 自选股配置文件格式错误，应为列表\n")
                return [], {}

            codes = []
            strategy_map = {}  # {code: strategy} 用于策略覆盖

            for item in watchlist:
                if isinstance(item, str):
                    # 旧格式兼容：纯字符串
                    codes.append(item)
                elif isinstance(item, dict):
                    code = item.get("code", "")
                    if code:
                        codes.append(code)
                        strategy = item.get("strategy", "auto")
                        # 仅记录非 auto 策略，供后续 determine_market_regime 覆盖使用
                        if strategy != "auto":
                            strategy_map[code] = strategy

            return codes, strategy_map

    except Exception as e:
        sys.stderr.write(f"[WARN] 读取自选股配置文件失败: {e}\n")
        return [], {}


def _fetch_custom_watchlist_quotes(watchlist_codes):
    """
    拉取自定义自选股的实时行情数据。
    watchlist_codes: 股票代码列表，如 ["sh600519", "sz000001"]
    返回格式与 fetch_market_top_actives 一致的字典列表。
    """
    if not watchlist_codes:
        return []

    results = []

    # 使用新浪财经接口批量获取实时行情
    # 格式: sh600519,sz000001 -> sh600519,sz000001
    codes_str = ",".join(watchlist_codes)
    url = f"http://hq.sinajs.cn/list={codes_str}"

    resp = _safe_get("sina", url)
    if not resp:
        sys.stderr.write("[WARN] 自选股行情获取失败，使用模拟数据\n")
        # 降级到模拟数据
        for code in watchlist_codes:
            results.append({
                "code": code,
                "name": f"自选股_{code}",
                "price": 0.0,
                "change_pct": 0.0,
                "change": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "amount": 0.0,
                "turnover_rate": 0.0,
                "volume_ratio": 0.0,
                "sector": "未知",
                "data_source": "mock",
            })
        return results

    try:
        # 解析新浪返回的数据格式
        # 格式: var hq_str_sh600519="贵州茅台,1680.00,...";
        lines = resp.text.strip().split("\n")
        for line in lines:
            if not line or "=" not in line:
                continue

            # 提取股票代码
            code_part = line.split("=")[0].split("_")[-1]
            if code_part not in watchlist_codes:
                continue

            # 提取数据部分
            data_part = line.split('"')[1] if '"' in line else ""
            if not data_part:
                continue

            fields = data_part.split(",")
            if len(fields) < 32:
                continue

            name = fields[0]
            open_price = _safe_float(fields[1])
            prev_close = _safe_float(fields[2])
            current_price = _safe_float(fields[3])
            high = _safe_float(fields[4])
            low = _safe_float(fields[5])
            volume = int(_safe_float(fields[8]))
            amount = _safe_float(fields[9])

            if current_price <= 0 or prev_close <= 0:
                continue

            change = current_price - prev_close
            change_pct = (change / prev_close) * 100 if prev_close > 0 else 0.0

            # 转换为 secid 格式 (用于后续技术指标计算)
            prefix = code_part[:2]
            code_raw = code_part[2:]
            secid = f"1.{code_raw}" if prefix == "sh" else f"0.{code_raw}"

            results.append({
                "code": code_part,
                "secid": secid,
                "name": name,
                "price": round(current_price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "volume": volume,
                "amount": round(amount, 2),
                "turnover_rate": 0.0,  # 新浪接口未直接提供，后续可补充
                "volume_ratio": 0.0,
                "sector": "自选股",
                "data_source": "custom_watchlist",
            })

    except Exception as e:
        sys.stderr.write(f"[WARN] 解析自选股行情数据失败: {e}\n")

    return results

# 全局线程池单例 (避免重复创建导致 OOM)
global_executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

# 反爬虫 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# 全局熔断状态字典
SOURCE_STATUS = {
    "eastmoney": True,
    "sina": True,
    "tencent": True,
    "netease": True,
}

# 数据源失败计数器
SOURCE_FAILURE_COUNT = {
    "eastmoney": 0,
    "sina": 0,
    "tencent": 0,
    "netease": 0,
}

# 常用标的兜底池
WATCHLIST = [
    {"code": "sh600519", "name": "贵州茅台", "base_price": 1680.00, "sector": "白酒"},
    {"code": "sz000858", "name": "五粮液", "base_price": 125.00, "sector": "白酒"},
    {"code": "sh601318", "name": "中国平安", "base_price": 48.50, "sector": "保险"},
    {"code": "sz000001", "name": "平安银行", "base_price": 11.20, "sector": "银行"},
    {"code": "sh600036", "name": "招商银行", "base_price": 35.80, "sector": "银行"},
    {"code": "sh601899", "name": "紫金矿业", "base_price": 16.50, "sector": "有色金属"},
    {"code": "sz002475", "name": "立讯精密", "base_price": 38.00, "sector": "消费电子"},
    {"code": "sz300059", "name": "东方财富", "base_price": 21.50, "sector": "券商"},
    {"code": "sh600900", "name": "长江电力", "base_price": 28.00, "sector": "电力"},
    {"code": "sz002594", "name": "比亚迪", "base_price": 275.00, "sector": "新能源汽车"},
    {"code": "sh600030", "name": "中信证券", "base_price": 22.00, "sector": "券商"},
    {"code": "sz000333", "name": "美的集团", "base_price": 62.00, "sector": "家电"},
    {"code": "sh601012", "name": "隆基绿能", "base_price": 23.00, "sector": "光伏"},
    {"code": "sz002415", "name": "海康威视", "base_price": 33.00, "sector": "安防"},
    {"code": "sh600276", "name": "恒瑞医药", "base_price": 45.00, "sector": "医药"},
    {"code": "sh600000", "name": "浦发银行", "base_price": 8.50, "sector": "银行"},
    {"code": "sz000568", "name": "泸州老窖", "base_price": 175.00, "sector": "白酒"},
    {"code": "sh601166", "name": "兴业银行", "base_price": 19.00, "sector": "银行"},
    {"code": "sz300750", "name": "宁德时代", "base_price": 210.00, "sector": "新能源"},
    {"code": "sh600938", "name": "中国海油", "base_price": 26.00, "sector": "石油"},
]


def _safe_float(val, default=0.0):
    """安全转换 float，失败返回 default"""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _get_headers():
    """生成反爬虫伪装请求头"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://finance.sina.com.cn/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _safe_get(source_name, url, params=None):
    """
    带熔断机制的安全 HTTP GET 请求
    连续失败 2 次触发熔断，后续直接跳过该数据源
    """
    if not SOURCE_STATUS.get(source_name, True):
        return None

    try:
        resp = http_session.get(url, params=params, headers=_get_headers(), timeout=3)
        resp.raise_for_status()
        # 请求成功，重置失败计数
        SOURCE_FAILURE_COUNT[source_name] = 0
        return resp
    except (TimeoutError, ConnectionError) as e:
        SOURCE_FAILURE_COUNT[source_name] += 1
        if SOURCE_FAILURE_COUNT[source_name] >= 2:
            SOURCE_STATUS[source_name] = False
            sys.stderr.write(f"[熔断] 数据源 {source_name} 连续失败 2 次，已熔断\n")
        return None
    except Exception:
        return None


def _fetch_kline_netease(stock_code, days=5):
    """
    网易财经备用源：获取日K线数据
    stock_code: 格式如 'sh600519' 或 'sz000858'
    """
    # 网易接口格式：sh600519 -> 0600519, sz000858 -> 1000858
    prefix = stock_code[:2]
    code = stock_code[2:]
    netease_code = f"0{code}" if prefix == "sh" else f"1{code}"

    url = f"https://quotes.money.163.com/service/chddata.html"
    params = {
        "code": netease_code,
        "start": datetime.now().strftime("%Y%m%d"),
        "end": (datetime.now() - pd.Timedelta(days=30)).strftime("%Y%m%d"),
        "fields": "TCLOSE;HIGH;LOW;TOPEN;LCLOSE;CHG;PCHG;TURNOVER;VOTURNOVER;VATURNOVER",
    }

    resp = _safe_get("netease", url, params)
    if not resp:
        return []

    try:
        # 网易返回 CSV 格式
        import io
        df = pd.read_csv(io.StringIO(resp.text), encoding="gb2312")
        if df.empty or len(df) < days:
            return []

        # 转换为腾讯接口格式: [date, open, close, high, low, volume]
        klines = []
        for _, row in df.head(days).iterrows():
            klines.append([
                str(row.get("日期", "")),
                float(row.get("开盘价", 0)),
                float(row.get("收盘价", 0)),
                float(row.get("最高价", 0)),
                float(row.get("最低价", 0)),
                float(row.get("成交量", 0)),
            ])
        return klines
    except Exception:
        return []


def _calc_technical_indicators(secid):
    """
    计算单只股票的技术指标 (MACD 趋势 / MA5 趋势)
    使用腾讯日线接口获取最近 K 线数据，失败时降级到网易备用源

    Returns:
        (macd_trend, ma5_trend) 元组，如 "金叉"/"红柱放大"/"死叉" 等
    """
    # 本地缓存：按股票代码+日期生成唯一文件名
    today = datetime.now().strftime("%Y%m%d")
    cache_file = os.path.join(CACHE_DIR, f"{secid.replace('.', '_')}_{today}_5day.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            klines = df.values.tolist()
            del df  # 释放 DataFrame 内存
            # 继续执行后续计算逻辑
        except Exception:
            klines = []
    else:
        klines = []

    if not klines:
        # 优先尝试腾讯源
        if SOURCE_STATUS.get("tencent", True):
            url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={secid},day,,,5,qt"
            resp = _safe_get("tencent", url)
            if resp:
                try:
                    data = resp.json()
                    klines = data.get("data", {}).get(secid, {}).get("qt", [])
                    if klines and len(klines) >= 5:
                        # 落盘缓存
                        df = pd.DataFrame(klines)
                        df.to_csv(cache_file, index=False)
                        del df
                except Exception:
                    klines = []

        # 腾讯失败，降级到网易备用源
        if not klines and SOURCE_STATUS.get("netease", True):
            # secid 格式: "1.600519" 或 "0.000858"，需要转换为 "sh600519" 格式
            stock_code = f"sh{secid.split('.')[1]}" if secid.startswith("1.") else f"sz{secid.split('.')[1]}"
            klines = _fetch_kline_netease(stock_code, days=5)
            if klines:
                # 落盘缓存
                df = pd.DataFrame(klines)
                df.to_csv(cache_file, index=False)
                del df

        if not klines:
            return ("无", "无")

    try:
        closes = [float(k[2]) for k in klines if len(k) > 2]
        if len(closes) < 5:
            return ("无", "无")

        # MA5 趋势
        ma5 = sum(closes[-5:]) / 5
        current_price = closes[-1]
        ma5_prev = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else ma5
        ma5_trend = "站上5日线" if current_price > ma5 else "跌破5日线"

        # 简易 MACD (EMA12-EMA26)
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = ema12 - ema26

        if len(closes) >= 12:
            ema12_prev = _ema(closes[:-1], 12)
            ema26_prev = _ema(closes[:-1], 26)
            dif_prev = ema12_prev - ema26_prev

            if dif > 0 and dif > dif_prev:
                macd_trend = "红柱放大"
            elif dif > 0 and dif < dif_prev:
                macd_trend = "红柱缩小"
            elif dif < 0 and dif < dif_prev:
                macd_trend = "绿柱放大"
            elif dif < 0 and dif > dif_prev:
                macd_trend = "绿柱缩小"
            else:
                macd_trend = "金叉" if dif_prev <= 0 and dif > 0 else "死叉"
        else:
            macd_trend = "无"

        return (macd_trend, ma5_trend)

    except Exception:
        return ("无", "无")


def _ema(data, period):
    """计算指数移动平均"""
    if len(data) < period:
        return sum(data) / len(data)
    multiplier = 2 / (period + 1)
    ema_val = sum(data[:period]) / period
    for price in data[period:]:
        ema_val = (price - ema_val) * multiplier + ema_val
    return ema_val


def get_sector_rankings():
    """抓取全市场行业板块今日涨跌幅排名"""
    # 本地缓存：按日期生成唯一文件名
    today = datetime.now().strftime("%Y%m%d")
    cache_file = os.path.join(CACHE_DIR, f"sector_rankings_{today}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            ranks = dict(zip(df["sector"], df["rank"]))
            return ranks
        except Exception:
            pass

    # 检查东方财富源是否熔断
    if not SOURCE_STATUS.get("eastmoney", True):
        sys.stderr.write("[WARN] 东方财富源已熔断，跳过板块排名获取\n")
        return {}

    url = "http://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fid": "f3",  # 按板块涨跌幅降序排名
        "fs": "m:90 t:2+f:!50",  # 东方财富的沪深行业板块特征码
        "fields": "f14,f3",  # 只需要板块名称和涨跌幅
    }
    try:
        resp = _safe_get("eastmoney", url, params)
        if not resp:
            return {}

        data = resp.json()
        ranks = {}
        for i, item in enumerate(data.get("data", {}).get("diff", [])):
            ranks[item["f14"]] = i + 1  # 记录排名，1 为当日最强板块

        # 落盘缓存
        if ranks:
            df = pd.DataFrame(list(ranks.items()), columns=["sector", "rank"])
            df.to_csv(cache_file, index=False)

        return ranks
    except Exception as e:
        sys.stderr.write(f"[WARN] 板块轮动排名获取失败: {e}\n")
        return {}


def fetch_market_top_actives(top_n=80):
    """
    第一级漏斗：广度抓取全市场资金最活跃的股票
    使用东方财富沪深A股排行接口，按成交额降序
    """
    # 本地缓存：按日期生成唯一文件名
    today = datetime.now().strftime("%Y%m%d")
    cache_file = os.path.join(CACHE_DIR, f"top_actives_{today}_{top_n}.csv")
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            cleaned_list = df.to_dict("records")
            return cleaned_list
        except Exception:
            pass

    # 检查东方财富源是否熔断
    if not SOURCE_STATUS.get("eastmoney", True):
        sys.stderr.write("[WARN] 东方财富源已熔断，跳过活跃股抓取\n")
        return []

    url = "http://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": str(top_n),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f6",  # 核心：按成交额降序排列，天然过滤缺资金的边缘股
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",  # 沪深A股
        "fields": "f2,f3,f4,f5,f6,f8,f10,f12,f14,f15,f16,f17,f100",  # 新增 f100 行业板块
    }

    try:
        response = _safe_get("eastmoney", url, params)
        if not response:
            return []

        data = response.json()

        if not data or not data.get("data") or not data["data"].get("diff"):
            return []

        raw_list = data["data"]["diff"]

        # 第二级漏斗：规则清洗
        cleaned_list = []
        for item in raw_list:
            name = item.get("f14", "")
            price = _safe_float(item.get("f2"))
            change_pct = _safe_float(item.get("f3"))

            # 剔除 ST股、退市股、停牌无价格股
            if "ST" in name or "退" in name or price <= 0:
                continue

            # 剔除一字涨停/跌停（普通散户买不进/卖不出，过滤掉）
            if abs(change_pct) >= 9.8 and item.get("f15") == item.get("f16"):
                continue

            code_raw = str(item.get("f12"))
            prefix = "sh" if code_raw.startswith("6") else "sz"
            secid = f"1.{code_raw}" if prefix == "sh" else f"0.{code_raw}"

            cleaned_list.append(
                {
                    "code": f"{prefix}{code_raw}",
                    "secid": secid,
                    "name": name,
                    "price": round(price, 2),
                    "change": _safe_float(item.get("f4")),
                    "change_pct": change_pct,
                    "open": _safe_float(item.get("f17"), price),
                    "high": _safe_float(item.get("f15"), price),
                    "low": _safe_float(item.get("f16"), price),
                    "volume": int(_safe_float(item.get("f5"))),
                    "amount": _safe_float(item.get("f6")),
                    "turnover_rate": _safe_float(item.get("f8")),
                    "volume_ratio": _safe_float(item.get("f10")),
                    "sector": item.get("f100", "未知"),
                }
            )

            # 限制交由 AI 深度处理的基数，单次扫描选前 40 只
            if len(cleaned_list) >= 40:
                break

        # 落盘缓存
        if cleaned_list:
            df = pd.DataFrame(cleaned_list)
            df.to_csv(cache_file, index=False)

        return cleaned_list

    except Exception as e:
        sys.stderr.write(f"[WARN] 市场全景数据获取失败: {e}\n")
        return []


def get_mock_quotes():
    """模拟实时数据（用于测试/非交易时间/接口失败降级）"""
    results = []
    for stock in WATCHLIST:
        change_pct = round(random.uniform(-3.0, 3.0), 2)
        price = round(stock["base_price"] * (1 + change_pct / 100), 2)

        if stock["code"].startswith("30") or stock["code"].startswith("688"):
            limit = 20.0
        else:
            limit = 10.0

        if abs(change_pct) > limit:
            change_pct = limit if change_pct > 0 else -limit
            price = round(stock["base_price"] * (1 + change_pct / 100), 2)

        results.append(
            {
                "code": stock["code"],
                "name": stock["name"],
                "price": price,
                "base_price": stock["base_price"],
                "change_pct": round(change_pct, 2),
                "change_amount": round(price - stock["base_price"], 2),
                "open": round(stock["base_price"] * (1 + random.uniform(-2, 2) / 100), 2),
                "high": price,
                "low": price,
                "volume": int(random.uniform(10000, 1000000)),
                "amount": round(price * random.uniform(10000, 1000000), 0),
                "sector": stock["sector"],
                "limit_up": abs(change_pct) >= limit * 0.95 and change_pct > 0,
                "limit_down": abs(change_pct) >= limit * 0.95 and change_pct < 0,
                "data_source": "mock",
                "macd_trend": "无",
                "ma5_trend": "无",
                "turnover_rate": 0,
                "volume_ratio": 0,
                "main_fund": 0,
            }
        )

    results.sort(key=lambda x: x["change_pct"], reverse=True)
    return results


# TTL 缓存机制 (替代简单的全局变量)
class TTLCache:
    """带过期时间和容量限制的缓存"""
    def __init__(self, ttl_seconds=10, max_size=100):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache = {}
        self._timestamps = {}

    def get(self, key):
        if key in self._cache:
            age = time.time() - self._timestamps[key]
            if age < self.ttl_seconds:
                return self._cache[key]
            else:
                # 过期清理
                del self._cache[key]
                del self._timestamps[key]
        return None

    def set(self, key, value):
        # 容量检查：超过 max_size 时清理最旧的数据
        if len(self._cache) >= self.max_size:
            self._evict_oldest()
        self._cache[key] = value
        self._timestamps[key] = time.time()

    def _evict_oldest(self):
        """清理最旧的缓存条目"""
        if not self._timestamps:
            return
        oldest_key = min(self._timestamps, key=self._timestamps.get)
        del self._cache[oldest_key]
        del self._timestamps[oldest_key]

    def clear(self):
        self._cache.clear()
        self._timestamps.clear()

# 10秒防封杀缓存（限制最多100个键）
_quotes_cache = TTLCache(ttl_seconds=10, max_size=100)

# 自选股策略覆盖映射：{code: strategy}，供 live_trader 在 determine_market_regime 时覆盖默认识别
_custom_strategy_map = {}


def get_custom_strategy_map():
    """获取自选股策略覆盖映射，供外部模块（如 live_trader）在策略路由时使用。"""
    return _custom_strategy_map


def get_realtime_quotes():
    """获取实时行情主控 - 全市场三级漏斗并发组装 (带10秒防封杀缓存)"""
    global _custom_strategy_map

    # 检查TTL缓存
    cached_result = _quotes_cache.get("realtime_quotes")
    if cached_result is not None:
        return cached_result

    sys.stderr.write("[INFO] 正在扫描全市场资金流向与板块轮动强度...\n")
    candidates = fetch_market_top_actives(top_n=80)
    sector_ranks = get_sector_rankings()  # 拉取全市场板块梯队排名

    # ===== 自定义自选股白名单注入 =====
    custom_codes, strategy_map = _load_custom_watchlist()
    _custom_strategy_map = strategy_map  # 更新全局策略映射，供 live_trader 读取

    if custom_codes:
        sys.stderr.write(f"[INFO] 检测到 {len(custom_codes)} 只自选股，正在强制拉取实时数据...\n")
        custom_quotes = _fetch_custom_watchlist_quotes(custom_codes)

        if custom_quotes:
            # 合并去重：以 code 为键，自选股优先覆盖
            merged_map = {item["code"]: item for item in candidates}
            for item in custom_quotes:
                if item["code"] not in merged_map:
                    merged_map[item["code"]] = item
            candidates = list(merged_map.values())
            sys.stderr.write(f"[INFO] 自选股注入完成，合并后候选池: {len(candidates)} 只\n")

    if not candidates:
        sys.stderr.write("[WARN] 市场扫描失败，启用本地 WATCHLIST 模拟数据兜底\n")
        result = get_mock_quotes()
        _quotes_cache.set("realtime_quotes", result)
        return result

    sys.stderr.write(
        f"[INFO] 过滤得到 {len(candidates)} 只活跃标的，正在并发计算技术因子与资金面...\n"
    )

    # 使用全局线程池单例 (避免重复创建)
    future_to_item = {
        global_executor.submit(_calc_technical_indicators, item["secid"]): item
        for item in candidates
    }

    for future in concurrent.futures.as_completed(future_to_item):
        item = future_to_item[future]
        macd_trend, ma5_trend = future.result()

        item["macd_trend"] = macd_trend
        item["ma5_trend"] = ma5_trend
        item["main_fund"] = get_main_fund_flow(item["secid"])
        # 将板块排名烙印在个股基因里（如果没有匹配到，默认给99名避险）
        item["sector_rank"] = sector_ranks.get(item["sector"], 99)

        limit = 20.0 if item["code"].startswith(("sz30", "sh688")) else 10.0
        item["limit_up"] = item["change_pct"] >= limit * 0.95
        item["limit_down"] = item["change_pct"] <= -limit * 0.95
        item["data_source"] = "market_scanner"

    # ===== 强化趋势底线与资金面一票否决过滤 =====
    # 仅保留：红盘（当日上涨）且站在5日线上方，且主力资金未大幅流出的标的
    filtered_candidates = []
    for item in candidates:
        # 趋势底线：必须是红盘且站上5日线
        if item["change_pct"] <= 0 or item["ma5_trend"] != "站上5日线":
            continue
        # 资金面一票否决：主力资金流出超过阈值（如 -500万）
        if item.get("main_fund", 0) < -500:
            continue
        filtered_candidates.append(item)

    if not filtered_candidates:
        sys.stderr.write("[WARN] 强化筛选后无标的进入 AI 打分环节，使用全部候选池保底\n")
        filtered_candidates = candidates

    # 限制送入大模型的标的数量（避免 Token 消耗过大，维持 15-20 只）
    if len(filtered_candidates) > 20:
        filtered_candidates = filtered_candidates[:20]

    filtered_candidates.sort(key=lambda x: x["turnover_rate"], reverse=True)

    # 写入TTL缓存
    _quotes_cache.set("realtime_quotes", filtered_candidates)
    return filtered_candidates


if __name__ == "__main__":
    quotes = get_realtime_quotes()
    print(json.dumps(quotes, ensure_ascii=False, indent=2))
