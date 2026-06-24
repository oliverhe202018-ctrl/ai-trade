import json
from pathlib import Path

def generate_mock_backtest_stats():
    stats = {
        "regimes": {
            "RISK_ON": {
                "trend_follow": {"win_rate": 0.68, "avg_return": 0.042},
                "mean_reversion": {"win_rate": 0.45, "avg_return": -0.015},
                "grid_c2": {"win_rate": 0.55, "avg_return": 0.012}
            },
            "CAUTION": {
                "trend_follow": {"win_rate": 0.42, "avg_return": -0.021},
                "mean_reversion": {"win_rate": 0.62, "avg_return": 0.025},
                "grid_c2": {"win_rate": 0.71, "avg_return": 0.015}
            },
            "RISK_OFF": {
                "trend_follow": {"win_rate": 0.15, "avg_return": -0.065},
                "mean_reversion": {"win_rate": 0.35, "avg_return": -0.035},
                "grid_c2": {"win_rate": 0.45, "avg_return": -0.010}
            }
        }
    }
    
    # 模拟在项目根目录下运行
    base_dir = Path(__file__).resolve().parent.parent
    cache_dir = base_dir / "data_cache"
    cache_dir.mkdir(exist_ok=True)
    stats_file = cache_dir / "backtest_stats.json"
    
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    print(f"回测胜率数据已生成至: {stats_file}")

if __name__ == "__main__":
    generate_mock_backtest_stats()

