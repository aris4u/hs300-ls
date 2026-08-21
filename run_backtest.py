"""跑项目二回测。

用法：
    python run_backtest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from hs300_ls.book import run_backtest
    from hs300_ls.frontier import run_frontier

    run_backtest()
    run_frontier()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
