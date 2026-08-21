"""跑项目二回测。

用法：
    python run_backtest.py
    python run_backtest.py --refresh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="跑项目二回测")
    parser.add_argument("--refresh", action="store_true", help="先重拉日K再回测")
    args = parser.parse_args()
    if args.refresh:
        from hs300_ls.prices import refresh_prices

        refresh_prices(force=True)
    from hs300_ls.book import run_backtest
    from hs300_ls.frontier import run_frontier

    run_backtest()
    run_frontier()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
