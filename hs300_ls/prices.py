"""只读项目一的价格缓存。不跑通达信公式。"""

from __future__ import annotations

import pickle

import pandas as pd

from hs300_ls.config import PRICE_CACHE, VIBE2_ROOT

MEMBERS_CSV = VIBE2_ROOT / "data" / "hs300_members.csv"


def _load_names(raw: dict) -> dict[str, str]:
    names = {str(k): str(v) for k, v in (raw.get("names") or {}).items()}
    if names:
        return names
    if not MEMBERS_CSV.exists():
        return {}
    df = pd.read_csv(MEMBERS_CSV)
    return {str(r["ts_code"]): str(r["name"]) for _, r in df.iterrows()}


def load_prices() -> dict:
    if not PRICE_CACHE.exists():
        raise FileNotFoundError(
            f"找不到价格缓存 {PRICE_CACHE}。请先在项目一 vibe2 里准备好日K。"
            f"当前 VIBE2_ROOT={VIBE2_ROOT}"
        )
    raw = pickle.loads(PRICE_CACHE.read_bytes())
    cal = pd.DatetimeIndex(raw["cal"])
    return {
        "cal": cal,
        "open": raw["open"].reindex(cal),
        "close": raw["close"].reindex(cal),
        "idx_open": pd.Series(raw["idx_open"]).reindex(cal),
        "idx_close": pd.Series(raw["idx_close"]).reindex(cal),
        "n_stocks": int(raw.get("n_stocks") or raw["close"].shape[1]),
        "names": _load_names(raw),
        "source": str(PRICE_CACHE),
    }
