"""探测本机通达信行情是否连上。只报连接状态，不把盘中价写入日K。"""

from __future__ import annotations

import sys
from hs300_ls.config import VIBE2_ROOT
from hs300_ls.download import now_cn


def _quote_time() -> str:
    return now_cn().strftime("%H:%M:%S")


def _via_vibe2(codes: list[str]) -> dict[str, dict]:
    root = str(VIBE2_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from hs300_strategy.tdx_l2 import snap_quotes

    return snap_quotes(codes)


def _via_mootdx(codes: list[str]) -> dict[str, dict]:
    from mootdx.quotes import Quotes

    client = Quotes.factory(market="std", timeout=5)
    symbols = []
    lookup = {}
    for ts in codes:
        num, mkt = ts.split(".")
        sym = f"sh{num}" if mkt.upper() == "SH" and num.startswith("000") else num
        symbols.append(sym)
        lookup[num] = ts
    part = client.quotes(symbol=symbols)
    if part is None or part.empty:
        return {}
    out = {}
    for _, row in part.iterrows():
        code = str(row.get("code", "")).zfill(6)
        ts = lookup.get(code)
        if not ts:
            continue
        price = row.get("price")
        if price is None:
            continue
        prev = row.get("last_close")
        pct = None
        try:
            p = float(price)
            if prev is not None and float(prev) > 0:
                pct = (p - float(prev)) / float(prev)
            out[ts] = {"price": p, "pct": pct, "bid1": row.get("bid1"), "ask1": row.get("ask1")}
        except (TypeError, ValueError):
            continue
    return out


def tdx_snap(codes: list[str] | None = None) -> dict:
    codes = codes or ["000300.SH"]
    now = now_cn()
    try:
        quotes = _via_vibe2(codes)
    except Exception:
        try:
            quotes = _via_mootdx(codes)
        except Exception as exc:
            return {
                "ok": False,
                "connected": False,
                "label": "通达信未连接",
                "error": str(exc)[:120],
                "quote_time": now.strftime("%H:%M:%S"),
                "quotes": {},
            }
    hs = quotes.get("000300.SH") or next(iter(quotes.values()), None)
    connected = bool(hs and hs.get("price") is not None)
    return {
        "ok": connected,
        "connected": connected,
        "label": "通达信已连接" if connected else "通达信未连接",
        "quote_time": now.strftime("%H:%M:%S"),
        "quotes": quotes,
        "error": None if connected else "没有沪深300盘口",
    }
