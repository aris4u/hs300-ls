"""价格面板：优先本仓库日K，没有再自己拉 BaoStock。项目一 pickle 只作后备。"""

from __future__ import annotations

import pickle

import pandas as pd

from hs300_ls.config import MEMBERS_FILE, OUTPUT_DIR, UNIVERSE_CACHE, VIBE2_PRICE_CACHE
from hs300_ls.download import download_prices, load_from_csv, local_csv_ready


def _from_pickle(path) -> dict:
    raw = pickle.loads(path.read_bytes())
    cal = pd.DatetimeIndex(raw["cal"])
    names = {str(k): str(v) for k, v in (raw.get("names") or {}).items()}
    if not names and MEMBERS_FILE.exists():
        df = pd.read_csv(MEMBERS_FILE)
        names = {str(r["ts_code"]): str(r["name"]) for _, r in df.iterrows()}
    return {
        "cal": cal,
        "open": raw["open"].reindex(cal),
        "close": raw["close"].reindex(cal),
        "idx_open": pd.Series(raw["idx_open"]).reindex(cal),
        "idx_close": pd.Series(raw["idx_close"]).reindex(cal),
        "n_stocks": int(raw.get("n_stocks") or raw["close"].shape[1]),
        "names": names,
        "source": str(raw.get("source") or path),
    }


def _save(uni: dict) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    UNIVERSE_CACHE.write_bytes(pickle.dumps(uni, protocol=pickle.HIGHEST_PROTOCOL))
    return uni


def refresh_prices(*, force: bool = False) -> dict:
    print("下载日K（BaoStock，写入本仓库 data/）…")
    return _save(download_prices(force=force))


def load_prices(*, refresh: bool = False) -> dict:
    if refresh:
        return refresh_prices(force=True)
    if UNIVERSE_CACHE.exists():
        return _from_pickle(UNIVERSE_CACHE)
    if local_csv_ready():
        print("从本仓库 CSV 组装价格面板")
        return _save(load_from_csv())
    if VIBE2_PRICE_CACHE.exists():
        print(
            f"本仓库还没有日K，先用项目一缓存 {VIBE2_PRICE_CACHE}。"
            "要改成本仓库自己拉数，运行 python run_download.py"
        )
        return _from_pickle(VIBE2_PRICE_CACHE)
    try:
        print("本仓库还没有日K，开始用 BaoStock 下载（第一次大约十几分钟）…")
        return _save(download_prices(force=False))
    except Exception as exc:
        raise RuntimeError(
            "无法准备价格。请运行 python run_download.py（需联网，BaoStock）。"
            f" 下载失败原因：{exc}"
        ) from exc
