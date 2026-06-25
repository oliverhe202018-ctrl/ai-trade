"""
Phase 7 回归测试: paper_trade_engine.py 模拟盘撮合闭环

验证:
  T1 - 无持仓 SELL → REJECTED, filled_qty=0, avg_price=0.0
  T2 - 正常 BUY → FILLED, filled_qty>0, avg_price>0
  T3 - REJECTED 不污染 paper_portfolio
  T4 - paper_trade_fills.jsonl 包含全部 10 个必要字段

运行: python tests/test_phase7_paper_engine.py
"""
import sys
import os
import json
import time
import subprocess
import tempfile
import shutil


def run_verification():
    T = tempfile.mkdtemp(prefix="hermes-verify-")
    os.makedirs(os.path.join(T, "data_cache"), exist_ok=True)

    SIGNAL_LOG  = os.path.join(T, "data_cache", "paper_signal_log.jsonl")
    PORTFOLIO   = os.path.join(T, "data_cache", "paper_portfolio.json")
    OFFSET_FILE = os.path.join(T, "data_cache", "paper_signal_log.offset")
    FILLS_FILE  = os.path.join(T, "data_cache", "paper_trade_fills.jsonl")

    # 写一个最小 wrapper，用临时路径覆盖 paper_trade_engine.py 的文件常量
    WRAPPER = os.path.join(T, "_runner.py")
    with open(WRAPPER, "w", encoding="utf-8") as f:
        f.write(fr"""
import os, sys
sys.path.insert(0, r"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}")
import paper_trade_engine as pte
pte.LOG_FILE       = r"{SIGNAL_LOG}"
pte.PORTFOLIO_FILE = r"{PORTFOLIO}"
pte.OFFSET_FILE    = r"{OFFSET_FILE}"
pte.FILLS_FILE     = r"{FILLS_FILE}"
pte.run_paper_trade_engine()
""".strip())

    ok = 0
    total = 0

    def check(label, condition, detail):
        nonlocal ok, total
        total += 1
        if condition:
            ok += 1
            print(f"  PASS  {label}: {detail}")
        else:
            print(f"  FAIL  {label}: {detail}")

    # ═══════════════════════════════════════════════════════
    #  T1: 无持仓 SELL → 期望 REJECTED
    # ═══════════════════════════════════════════════════════
    with open(SIGNAL_LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "code": "sz000858", "action": "SELL",
            "quantity": 100, "price": 145.0
        }, ensure_ascii=False) + "\n")

    proc = subprocess.Popen(
        [sys.executable, WRAPPER],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    time.sleep(4)
    proc.kill()
    proc.communicate()

    fills_t1 = [json.loads(line) for line in open(FILLS_FILE) if line.strip()]
    pf_t1 = json.load(open(PORTFOLIO))

    print("T1: sz000858 SELL (w/o position)")
    check("status=REJECTED",  fills_t1[0]["status"] == "REJECTED", fills_t1[0]["status"])
    check("filled_qty=0",     fills_t1[0]["filled_qty"] == 0,      fills_t1[0]["filled_qty"])
    check("avg_price=0.0",    fills_t1[0]["avg_price"] == 0.0,      fills_t1[0]["avg_price"])
    check("reason nonempty",  bool(fills_t1[0].get("reason")),      repr(fills_t1[0].get("reason")))

    # ═══════════════════════════════════════════════════════
    #  T2: 正常 BUY → 期望 FILLED
    # ═══════════════════════════════════════════════════════
    for fp in [SIGNAL_LOG, PORTFOLIO, OFFSET_FILE, FILLS_FILE]:
        if os.path.exists(fp):
            os.remove(fp)

    with open(SIGNAL_LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "code": "sz000002", "action": "BUY",
            "quantity": 500, "price": 10.0
        }, ensure_ascii=False) + "\n")

    proc2 = subprocess.Popen(
        [sys.executable, WRAPPER],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    time.sleep(4)
    proc2.kill()
    proc2.communicate()

    fills_t2 = [json.loads(line) for line in open(FILLS_FILE) if line.strip()]
    pf_t2 = json.load(open(PORTFOLIO))

    print("\nT2: sz000002 BUY 500@10")
    check("status=FILLED",   fills_t2[0]["status"] == "FILLED",  fills_t2[0]["status"])
    check("filled_qty=500",  fills_t2[0]["filled_qty"] == 500,   fills_t2[0]["filled_qty"])
    check("avg_price>0",     fills_t2[0]["avg_price"] > 0,        fills_t2[0]["avg_price"])
    check("pos sz000002",    "sz000002" in pf_t2["positions"],    str(pf_t2["positions"]))

    # ═══════════════════════════════════════════════════════
    #  T3: 拒单不污染 portfolio
    # ═══════════════════════════════════════════════════════
    print("\nT3: portfolio unchanged by REJECTED")
    check("T1 positions empty", len(pf_t1.get("positions", {})) == 0, str(pf_t1.get("positions")))
    check("T1 cash unchanged",  pf_t1["cash"] == 100_000.0,           pf_t1["cash"])

    # ═══════════════════════════════════════════════════════
    #  T4: fills 字段完整性
    # ═══════════════════════════════════════════════════════
    print("\nT4: fill entry schema")
    required_fields = [
        "timestamp", "code", "action", "quantity", "price",
        "order_id", "status", "filled_qty", "avg_price", "reason",
    ]
    missing = [k for k in required_fields if k not in fills_t2[0]]
    check("all 10 fields present", len(missing) == 0, missing if missing else "OK")

    # ── cleanup ──
    shutil.rmtree(T, ignore_errors=True)
    print(f"\n  = {ok}/{total} PASSED =")
    return ok == total


if __name__ == "__main__":
    sys.exit(0 if run_verification() else 1)
