"""项目二参数。事先固定，不对 2024-09 报告窗口搜参。

成交仍是 T 日收盘信号、T+1 开盘。
总敞口约 100%（多 70% / 空 30%），净敞口约 +40%。
弱市不空仓，只降到半仓。空头计印花税和年化融券费。
价格只读项目一已经下好的日K，不改项目一文件。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

VIBE2_ROOT = Path(os.environ.get("VIBE2_ROOT", ROOT.parent / "vibe2")).resolve()
PRICE_CACHE = VIBE2_ROOT / "output" / "enhance_opt" / "universe_v1.pkl"
STOCK_DIR = VIBE2_ROOT / "data" / "stocks"

FULL_START = "2010-07-01"
TRAIN_START = "2010-07-01"
TRAIN_END = "2020-12-31"
TEST_START = "2021-01-01"
REPORT_START = "2024-09-02"

TOP_N = 5
SHORT_N = 5
ALLOW_SHORT = True
MIN_LONG = 3
MOM_LOOKBACK = 20
MOM_SKIP = 1
INDEX_MA = 60
REBALANCE_BARS = 21

# 70/30 of 100% gross. Not searched on the report window.
LONG_GROSS = 0.70
SHORT_GROSS = 0.30
RISK_OFF_SCALE = 0.50  # below MA: half size, never full cash

COMMISSION = 0.0003
SLIPPAGE_BUY = 0.0005
SLIPPAGE_SELL = 0.0005
STAMP_TAX = 0.001
BORROW_ANNUAL = 0.05

SCHEME_ID = "ideal_ls"
SCHEME_NAME = "理想约束 · 净多头动量"
PORT = 8766

EXECUTION_NOTE = (
    "T日收盘生成信号，T+1日开盘成交。"
    "总敞口约 100%：做多约 70%、做空约 30%。"
    "沪深300低于60日均线时降到半仓，不空仓。"
    "卖出含印花税 10bp，空头另计年化 5% 融券费。"
    "不是大A可执行结果，不能和项目一的 70/30 超额直接比。"
)
