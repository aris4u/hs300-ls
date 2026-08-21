"""下载沪深300成分前复权日K到本仓库 data/。

用法：
    python run_download.py
    python run_download.py --force
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
    parser = argparse.ArgumentParser(description="下载项目二日K")
    parser.add_argument("--force", action="store_true", help="忽略已有 CSV，全量重拉")
    args = parser.parse_args()
    from hs300_ls.prices import refresh_prices

    uni = refresh_prices(force=args.force)
    print(f"来源 {uni['source']}")
    print(f"股票 {uni['n_stocks']}  日历 {uni['cal'].min().date()} ~ {uni['cal'].max().date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
