"""
左侧均值回归抄底模型 - 寻找"主线板块错杀股"

核心逻辑：
- 赛道强（sector_rank前5）
- 价格弱（连跌3天 + 跌破布林带下轨）
- 资金逆势（主力净流入为正 = 暗中吸筹）
- 三者共振 = 错杀信号
"""


class MeanReversionScore:
    def __init__(
        self,
        stock_code="",
        total_score=0.0,
        sector_rank=99,
        consecutive_down_days=0,
        below_boll_lower=False,
        fund_flow_net=0.0,
        price_deviation_pct=0.0,
        signals=None,
        buy_reason="",
    ):
        self.stock_code = stock_code
        self.total_score = total_score
        self.sector_rank = sector_rank
        self.consecutive_down_days = consecutive_down_days
        self.below_boll_lower = below_boll_lower
        self.fund_flow_net = fund_flow_net
        self.price_deviation_pct = price_deviation_pct
        self.signals = signals or []
        self.buy_reason = buy_reason


class MeanReversionScanner:
    """
    左侧抄底模型：寻找"主线板块错杀股"
    """

    TOP_SECTOR_RANK = 5  # 只看板块前N名的股票
    MIN_DOWN_DAYS = 3  # 最少连续下跌天数
    BOLL_PERIOD = 20  # 布林带周期
    BOLL_STD_MULT = 2.0  # 布林带标准差倍数
    MIN_FUND_FLOW = -0.5  # 主力净流入最低要求（允许微幅流出洗盘）
    MAX_DEVIATION = -0.05  # 偏离MA20至少-5%才算超跌
    MIN_SCORE = 75.0  # 最低综合得分阈值
    DEEP_BOLL_TOLERANCE = 0.03  # 跌破布林下轨超3%触发深度超跌豁免
    MA_LONG_PERIOD = 60  # 长线趋势锁周期（MA60）
    VOLUME_MA_PERIOD = 5  # 地量过滤：过去5天平均成交量

    def calc_ma(self, prices, period):
        """计算简单移动平均线"""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def calc_bollinger_bands(self, prices):
        """计算布林带"""
        n = len(prices)
        if n < self.BOLL_PERIOD:
            return None

        window = prices[-self.BOLL_PERIOD:]
        ma = sum(window) / self.BOLL_PERIOD
        variance = sum((x - ma) ** 2 for x in window) / self.BOLL_PERIOD
        std = variance ** 0.5

        return {
            "upper": ma + self.BOLL_STD_MULT * std,
            "mid": ma,
            "lower": ma - self.BOLL_STD_MULT * std,
        }

    def count_consecutive_down_days(self, prices):
        """从最新一天往前数连续下跌天数"""
        count = 0
        for i in range(len(prices) - 1, 0, -1):
            if prices[i] < prices[i - 1]:
                count += 1
            else:
                break
        return count

    def score_stock(self, stock_code, close_prices, fund_flows, sector_rank, volumes=None):
        """
        给单只股票打分，不满足核心条件返回 None

        Args:
            stock_code: 股票代码
            close_prices: 最近60日收盘价 list
            fund_flows: 最近60日主力净流入（万） list
            sector_rank: 当前板块在全市场排名
            volumes: 最近60日成交量 list（可选，用于地量过滤）
        """
        signals = []
        score = 0.0

        # P2-2 修复：显式长度断言防御，拦截数据不足的新股或停牌股
        if not close_prices or len(close_prices) < 60:
            return None  # 数据不足60天，无法计算MA60

        # 硬性条件1：必须在主线板块（sector_rank前5）
        if sector_rank > self.TOP_SECTOR_RANK:
            return None

        # 硬性条件2：计算布林带，必须跌破下轨
        if len(close_prices) < self.BOLL_PERIOD:
            return None
        boll = self.calc_bollinger_bands(close_prices)
        if not boll:
            return None

        current_price = close_prices[-1]
        boll_lower = boll["lower"]
        ma20 = boll["mid"]

        if current_price >= boll_lower:
            return None  # 没跌破下轨，不是超跌状态

        # 硬性条件3：连续下跌至少N天
        if len(close_prices) < 4:
            return None
        down_days = self.count_consecutive_down_days(close_prices)
        if down_days < self.MIN_DOWN_DAYS:
            return None

        # P0硬性条件4：MA60长线趋势锁（只做多头区间的短期错杀回调）
        ma60 = self.calc_ma(close_prices, self.MA_LONG_PERIOD)
        if ma60 is None:
            return None  # 数据不足60天，无法计算MA60
        if current_price < ma60:
            return None  # 长期趋势空头，拒绝买入
        signals.append(f"MA60={ma60:.2f}（长期多头趋势确认）")

        # P0硬性条件5：地量过滤（缩量下跌才是洗盘，放量下跌是出货）
        if volumes is not None and len(volumes) > self.VOLUME_MA_PERIOD:
            latest_volume = volumes[-1]
            volume_ma = sum(volumes[-(self.VOLUME_MA_PERIOD + 1):-1]) / self.VOLUME_MA_PERIOD
            if latest_volume >= volume_ma:
                return None  # 放量下跌，不是洗盘
            signals.append(f"地量确认：当日成交量{latest_volume:.0f} < 5日均量{volume_ma:.0f}")
        else:
            signals.append("无成交量数据，跳过地量过滤")

        # 计算跌破布林下轨深度
        boll_pct = boll_lower / current_price - 1
        deep_boll_oversold = boll_pct > self.DEEP_BOLL_TOLERANCE

        # 硬性条件4：主力净流入为正（带深度超跌豁免机制）
        latest_fund_flow = fund_flows[-1] if fund_flows else 0

        if deep_boll_oversold:
            # 深度超跌豁免：跌破布林下轨超3%，无视主力资金流出量
            fund_flow_ok = True
            signals.append(f"深度跌破布林下轨{boll_pct:.1%}，豁免资金面审查")
        else:
            # 常规模式：要求资金面达标
            fund_flow_ok = latest_fund_flow > self.MIN_FUND_FLOW
            if not fund_flow_ok:
                return None  # 主力也在跑，不是错杀，是真跌

        # 加分项

        # 板块排名越高加分越多
        score += (self.TOP_SECTOR_RANK - sector_rank + 1) * 10  # 1名=50分, 5名=10分
        signals.append(f"板块Top{sector_rank}（主线赛道）")

        # 超跌幅度加分
        deviation_pct = (current_price - ma20) / ma20
        deviation_score = min(abs(deviation_pct) * 200, 20)
        score += deviation_score
        signals.append(f"偏离MA20 {deviation_pct:.1%}（超跌程度）")

        # 连跌天数加分
        down_score = min((down_days - self.MIN_DOWN_DAYS) * 3, 9)
        score += 5 + down_score
        signals.append(f"连续下跌{down_days}天")

        # 布林带超跌深度加分
        score += min(abs(boll_pct) * 100, 10)
        signals.append(f"跌破布林下轨{abs(boll_pct):.1%}")

        # 主力净流入加分（正值加分，负值轻微扣分但不淘汰）
        if latest_fund_flow > 0:
            flow_score = min(latest_fund_flow * 0.0002, 15)
            score += flow_score
            signals.append(f"主力净流入{latest_fund_flow:.1f}万（逆势吸筹）")
        else:
            # 微幅流出仅扣分不淘汰
            flow_penalty = min(abs(latest_fund_flow) * 0.0001, 5)
            score -= flow_penalty
            signals.append(f"主力净流出{abs(latest_fund_flow):.1f}万（深度超跌豁免扣分）")

        # 近3日主力持续流入
        if len(fund_flows) >= 3 and all(f > 0 for f in fund_flows[-3:]):
            score += 10
            signals.append("连续3日主力净流入（持续吸筹信号")
        elif deep_boll_oversold:
            score += 3  # 深度超跌给少量补偿分
            signals.append("极端恐慌错杀（深度超跌补偿分）")

        if latest_fund_flow > 0:
            buy_reason = (
                f"【左侧抄底-MA60多头区间+缩量超跌】"
                f"板块Top{sector_rank}主线赛道，"
                f"MA60={ma60:.2f}确认长期多头趋势，"
                f"连跌{down_days}天跌破布林下轨（短期错杀），"
                f"地量确认缩量洗盘，"
                f"主力净流入{latest_fund_flow:.1f}万逆势吸筹，"
                f"综合评分{score:.0f}分"
            )
        else:
            buy_reason = (
                f"【左侧抄底-深度超跌豁免】"
                f"板块Top{sector_rank}主线赛道，"
                f"MA60={ma60:.2f}确认长期多头趋势，"
                f"连跌{down_days}天跌破布林下轨{boll_pct:.1%}，"
                f"地量确认缩量洗盘，"
                f"深度超跌豁免资金面审查，"
                f"主力微幅流出{latest_fund_flow:.1f}万不计入淘汰条件，"
                f"综合评分{score:.0f}分"
            )

        return MeanReversionScore(
            stock_code=stock_code,
            total_score=score,
            sector_rank=sector_rank,
            consecutive_down_days=down_days,
            below_boll_lower=True,
            fund_flow_net=latest_fund_flow,
            price_deviation_pct=deviation_pct,
            signals=signals,
            buy_reason=buy_reason,
        )

    def scan_universe(self, stocks_data, min_score=None):
        """
        扫描全市场候选股，返回按评分排序的买入候选列表

        Args:
            stocks_data: [{'code': 'sh600xxx', 'close': [60日收盘价],
                          'fund_flow': [60日主力净流入], 'sector_rank': int,
                          'volume': [60日成交量]}, ...]
            min_score: 最低评分阈值（默认50）
        """
        if min_score is None:
            min_score = self.MIN_SCORE

        candidates = []
        for stock in stocks_data:
            result = self.score_stock(
                stock_code=stock["code"],
                close_prices=stock["close"],
                fund_flows=stock["fund_flow"],
                sector_rank=stock["sector_rank"],
                volumes=stock.get("volume"),
            )
            if result and result.total_score >= min_score:
                candidates.append(result)

        candidates.sort(key=lambda x: x.total_score, reverse=True)
        return candidates


if __name__ == "__main__":
    print("MeanReversionScanner 已加载")
    print("使用方式: scanner = MeanReversionScanner() -> scanner.scan_universe(data)")
