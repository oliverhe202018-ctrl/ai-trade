"""
模拟盘撮合引擎 (Paper Trade Engine) — Phase 7 Final
职责：
1. 循环读取 data_cache/paper_signal_log.jsonl（带 offset 断点续读）。
2. 使用 MockBrokerAdapter 进行撮合。
3. 所有撮合结果写入 data_cache/paper_trade_fills.jsonl。
4. 资产状态原子写入 data_cache/paper_portfolio.json，与实盘严格物理隔离。
"""
import os
import sys
import re
import time
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger
from core.broker_adapter import MockBrokerAdapter
from core.state_manager import load_portfolio as _lp
from core.state_manager import save_portfolio as _sp

# ── 文件路径 ──────────────────────────────────────────────
LOG_FILE       = os.path.join(PROJECT_ROOT, "data_cache", "paper_signal_log.jsonl")
PORTFOLIO_FILE = os.path.join(PROJECT_ROOT, "data_cache", "paper_portfolio.json")
OFFSET_FILE    = os.path.join(PROJECT_ROOT, "data_cache", "paper_signal_log.offset")
FILLS_FILE     = os.path.join(PROJECT_ROOT, "data_cache", "paper_trade_fills.jsonl")

# ── 股票代码正则 (sh/sz + 6位数字) ─────────────────────────
_CODE_RE = re.compile(r"^(sh|sz)\d{6}$")


# ═══════════════════════════════════════════════════════════
#  Offset 持久化
# ═══════════════════════════════════════════════════════════

def _load_offset() -> int:
    """从 offset 文件恢复上次读取位置，不存在则返回 0。"""
    if not os.path.exists(OFFSET_FILE):
        return 0
    try:
        with open(OFFSET_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if raw:
                return int(raw)
    except (ValueError, OSError) as e:
        logger.warning(f"[PAPER ENGINE] offset 文件损坏，从 0 开始: {e}")
    return 0


def _save_offset(offset: int) -> None:
    """原子化写入 offset 文件。"""
    tmp = OFFSET_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(offset))
        os.replace(tmp, OFFSET_FILE)
    except OSError as e:
        logger.error(f"[PAPER ENGINE] 写入 offset 失败: {e}")


# ═══════════════════════════════════════════════════════════
#  Portfolio 初始化 & 双向同步
# ═══════════════════════════════════════════════════════════

def _init_portfolio() -> dict:
    pf = _lp(PORTFOLIO_FILE)
    if not pf:
        pf = {"cash": 100_000.0, "positions": {}}
    if "cash" not in pf:
        pf["cash"] = 100_000.0
    if "positions" not in pf:
        pf["positions"] = {}
    return pf


def _sync_to_mock_broker(pf: dict, broker: MockBrokerAdapter):
    broker.balance["cash"] = pf.get("cash", 100_000.0)
    broker.positions = {}
    for code, pos in pf.get("positions", {}).items():
        qty = pos.get("quantity", 0)
        avg_cost = pos.get("avg_cost", 0.0)
        broker.positions[code] = {
            "code": code,
            "qty": qty,
            "avg_price": avg_cost,
            "market_value": qty * avg_cost,
        }


def _sync_to_portfolio(pf: dict, broker: MockBrokerAdapter):
    pf["cash"] = broker.balance["cash"]
    pf["positions"] = {}
    for code, pos in broker.positions.items():
        pf["positions"][code] = {
            "quantity": pos["qty"],
            "avg_cost": pos["avg_price"],
        }


# ═══════════════════════════════════════════════════════════
#  字段校验（增强版）
# ═══════════════════════════════════════════════════════════

def _validate_signal(order: dict) -> tuple[bool, str]:
    """
    返回 (是否通过, 失败原因)。

    校验规则：
      - code   → sh/sz + 6位数字
      - action → 仅 BUY / SELL
      - quantity → 正整数，且为 100 的整数倍
      - price  → > 0
      - 必要字段存在
    """
    # 1. 必要字段
    required_fields = ["code", "action", "quantity", "price"]
    missing = [k for k in required_fields if k not in order]
    if missing:
        return False, f"缺少必要字段: {missing}"

    # 2. code 格式
    code = str(order["code"]).strip().lower()
    if not _CODE_RE.match(code):
        return False, f"code 格式非法 (需 sh/sz + 6位数字): {order['code']}"

    # 3. action 枚举
    action = order["action"]
    if action == "GRID":
        return False, "GRID 信号暂不支持"
    if action not in ("BUY", "SELL"):
        return False, f"未知 action: {action}"

    # 4. quantity
    try:
        qty = int(order["quantity"])
    except (ValueError, TypeError):
        return False, f"quantity 无法转为整数: {order['quantity']}"

    if qty <= 0:
        return False, f"quantity 必须为正整数，实际: {qty}"
    if qty % 100 != 0:
        return False, f"quantity 必须为 100 的整数倍，实际: {qty}"

    # 5. price
    try:
        price = float(order["price"])
    except (ValueError, TypeError):
        return False, f"price 无法转为浮点数: {order['price']}"

    if price <= 0:
        return False, f"price 必须 > 0，实际: {price}"

    return True, ""


# ═══════════════════════════════════════════════════════════
#  信号处理（返回 (order_id, status)）
# ═══════════════════════════════════════════════════════════

def _process_signal(broker: MockBrokerAdapter, order: dict) -> tuple[str, str]:
    """
    下单并返回 (order_id, 初始状态)。
    状态可为 "PLACED"（下单成功）或 "FAILED"（如资金不足导致 REJECTED）。
    """
    code = order["code"]
    action = order["action"]
    qty = int(order["quantity"])
    price = float(order["price"])

    order_id = broker.place_order(code, action, qty, "市价", price)

    # 如果 broker 已标记为 REJECTED（如 MockBrokerAdapter 暂未做但未来可能）
    status = broker.orders.get(order_id, {}).get("status", "PLACED")
    return order_id, status


# ═══════════════════════════════════════════════════════════
#  订单状态归一化（修正 MockBrokerAdapter 预存问题）
# ═══════════════════════════════════════════════════════════

def _normalize_order_status(status_info: dict) -> dict:
    """
    对 broker.get_order_status() 返回的状态做统一归一化。

    MockBrokerAdapter 在拒单场景下可能先生成 filled_qty / avg_price，
    随后才判定 REJECTED。此函数确保：
      - REJECTED  → filled_qty=0, avg_price=0.0, reason 非空
      - FILLED    → 保留实际 filled_qty / avg_price
      - CANCELED  → filled_qty=0, avg_price=0.0
      - PENDING / PARTIAL_FILLED → 按实际状态透传，绝不伪装为 FILLED

    返回规范化后的 dict: {status, filled_qty, avg_price, reason}
    """
    status     = status_info.get("status", "PENDING")
    filled_qty = status_info.get("filled_qty", 0)
    avg_price  = status_info.get("avg_price", 0.0)
    reason     = ""

    if status == "REJECTED":
        filled_qty = 0
        avg_price  = 0.0
        reason     = (
            status_info.get("reason")
            or status_info.get("error")
            or status_info.get("message")
            or "REJECTED by MockBrokerAdapter"
        )
    elif status == "FILLED":
        # 保留 broker 返回的真实撮合数据
        pass
    elif status == "PARTIAL_FILLED":
        # 保留实际部分成交数据
        pass
    elif status == "CANCELED":
        filled_qty = 0
        avg_price  = 0.0
    # PENDING 保持原样

    return {
        "status":     status,
        "filled_qty": filled_qty,
        "avg_price":  avg_price,
        "reason":     reason,
    }


# ═══════════════════════════════════════════════════════════
#  成交流水写入
# ═══════════════════════════════════════════════════════════

def _append_fills_log(entries: list[dict]) -> None:
    """将成交记录追加写入 paper_trade_fills.jsonl。"""
    if not entries:
        return
    os.makedirs(os.path.dirname(FILLS_FILE), exist_ok=True)
    try:
        with open(FILLS_FILE, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error(f"[PAPER ENGINE] 写入成交流水失败: {e}")


def _build_fill_entry(
    order: dict,
    order_id: str,
    status: str,
    filled_qty: int,
    avg_price: float,
    reason: str = "",
) -> dict:
    return {
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code":         order["code"],
        "action":       order["action"],
        "quantity":     int(order["quantity"]),
        "price":        float(order["price"]),
        "order_id":     order_id,
        "status":       status,
        "filled_qty":   filled_qty,
        "avg_price":    round(avg_price, 3),
        "reason":       reason,
    }


# ═══════════════════════════════════════════════════════════
#  主循环
# ═══════════════════════════════════════════════════════════

def run_paper_trade_engine():
    logger.info("🛡️ [PAPER ENGINE] 独立模拟盘撮合引擎启动 (Phase 7 Final)...")

    broker = MockBrokerAdapter()

    # ── 断点续读：从 offset 文件恢复 ──
    last_position = _load_offset()
    if last_position > 0:
        logger.info(f"[PAPER ENGINE] 从 offset={last_position} 恢复断点续读")

    # 如果 offset 指向的位置 > 当前文件大小，说明信号文件已被截断/重建
    # 此时无法判断哪些行已处理，安全策略：重置 offset=0 全量重放。
    # MockBrokerAdapter 的 _filled 标记保证重复处理不产生副作用。
    if os.path.exists(LOG_FILE):
        current_size = os.path.getsize(LOG_FILE)
        if last_position > current_size:
            logger.warning(
                f"[PAPER ENGINE] offset({last_position}) > 文件大小({current_size})，"
                f"信号文件可能被截断/重建。重置 offset=0 全量重放（幂等安全）。"
            )
            last_position = 0
            _save_offset(0)
    elif last_position > 0:
        logger.warning(
            f"[PAPER ENGINE] offset({last_position}) > 0 但信号文件不存在，重置 offset=0"
        )
        last_position = 0
        _save_offset(0)

    while True:
        try:
            if not os.path.exists(LOG_FILE):
                time.sleep(2)
                continue

            current_size = os.path.getsize(LOG_FILE)

            if current_size < last_position:
                # 文件被截断或重建 → 从头读
                logger.warning("[PAPER ENGINE] 信号文件大小缩小，可能被截断，从 0 重新读取")
                last_position = 0

            if current_size == last_position:
                time.sleep(1)
                continue

            # ── 读取新增行 ──
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                f.seek(last_position)
                lines = f.readlines()
                new_position = f.tell()

            if not lines:
                time.sleep(1)
                continue

            # ── 加载/同步 portfolio ──
            pf = _init_portfolio()
            _sync_to_mock_broker(pf, broker)

            # ── 统计计数器 ──
            stats = {
                "processed": 0,   # JSON解析成功且字段校验通过
                "skipped":   0,   # 字段校验失败
                "failed":    0,   # JSON解析或执行异常
                "placed":    0,   # 成功下单 (PLACED)
                "filled":    0,   # get_order_status 后确认 FILLED
                "rejected":  0,   # REJECTED (资金不足等)
            }
            fill_entries: list[dict] = []
            tracked_order_ids: list[str] = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                order = None
                try:
                    order = json.loads(line)
                except json.JSONDecodeError:
                    logger.error(f"[PAPER ENGINE] JSON 解析失败，跳过: {line[:120]}")
                    stats["failed"] += 1
                    continue

                # ── 校验 ──
                valid, reason = _validate_signal(order)
                if not valid:
                    logger.warning(f"[PAPER ENGINE] 校验未通过: {reason} | order={order}")
                    stats["skipped"] += 1
                    # 跳过信号也写入成交流水（status=SKIPPED）
                    fill_entries.append(_build_fill_entry(
                        order, "", "SKIPPED", 0, 0.0, reason
                    ))
                    continue

                stats["processed"] += 1

                try:
                    order_id, place_status = _process_signal(broker, order)
                    tracked_order_ids.append(order_id)

                    if place_status == "REJECTED":
                        stats["rejected"] += 1
                        fill_entries.append(_build_fill_entry(
                            order, order_id, "REJECTED", 0, 0.0,
                            broker.orders.get(order_id, {}).get("reason", "资金不足")
                        ))
                    else:
                        stats["placed"] += 1
                except Exception as e:
                    logger.error(f"[PAPER ENGINE] 下单异常: {e} | order={order}")
                    stats["failed"] += 1
                    fill_entries.append(_build_fill_entry(
                        order, "", "FAILED", 0, 0.0, str(e)
                    ))

            # ── 仅对本次新增 order_id 做撮合刷新 ──
            if tracked_order_ids:
                for oid in tracked_order_ids:
                    try:
                        raw_status = broker.get_order_status(oid)
                    except Exception as e:
                        logger.error(f"[PAPER ENGINE] get_order_status 异常: {e} | oid={oid}")
                        continue

                    # 归一化订单状态（修正 MockBrokerAdapter REJECTED 脏数据）
                    n = _normalize_order_status(raw_status)
                    final_status = n["status"]
                    filled_qty   = n["filled_qty"]
                    avg_price    = n["avg_price"]
                    reason       = n["reason"]

                    if final_status == "FILLED":
                        stats["filled"] += 1
                    elif final_status == "REJECTED":
                        stats["rejected"] += 1

                    # 写 fill 记录
                    order_ref = broker.orders.get(oid, {})
                    code   = order_ref.get("code", "")
                    action = order_ref.get("action", "")
                    qty    = order_ref.get("qty", 0)
                    price  = order_ref.get("price", 0.0)
                    fill_entries.append(_build_fill_entry(
                        {"code": code, "action": action, "quantity": qty, "price": price},
                        oid, final_status, filled_qty, avg_price, reason
                    ))

                # ── 同步 portfolio ──
                _sync_to_portfolio(pf, broker)
                _sp(pf, PORTFOLIO_FILE)

            # ── 写入成交流水 ──
            _append_fills_log(fill_entries)

            # ── 持久化 offset ──
            _save_offset(new_position)
            last_position = new_position

            # ── 汇总日志 ──
            logger.info(
                f"🛡️ [PAPER ENGINE] 本轮统计 → "
                f"processed={stats['processed']}, "
                f"placed={stats['placed']}, "
                f"filled={stats['filled']}, "
                f"rejected={stats['rejected']}, "
                f"skipped={stats['skipped']}, "
                f"failed={stats['failed']}"
                f" | offset={new_position}"
            )

        except Exception as e:
            logger.error(f"[PAPER ENGINE] 运行时异常: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_paper_trade_engine()
