"""理想约束选股界面。

用法：
    python app.py
    python app.py --port 8766 --no-browser
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hs300_ls.ui_server import main

if __name__ == "__main__":
    raise SystemExit(main())
