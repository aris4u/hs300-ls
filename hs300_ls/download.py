"""用 BaoStock 拉沪深300成分和前复权日K。缓存到本仓库 data/，不写项目一。"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
import pandas as pd

from hs300_ls.config import DATA_DIR, INDEX_FILE, MEMBERS_FILE, PRICE_START, STOCK_DIR

BAR_READY = dtime(15, 20)


def now_cn() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return datetime.utcnow() + timedelta(hours=8)


def last_closed_session(now: datetime | None = None) -> date:
    now = now or now_cn()
    d = now.date()
    if now.weekday() >= 5:
        return d - timedelta(days=now.weekday() - 4)
    if now.time() < BAR_READY:
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    return d


def official_end_key(end: str | None = None) -> str:
    end_key = (end or date.today().strftime("%Y%m%d")).replace("-", "")
    return min(end_key, last_closed_session().strftime("%Y%m%d"))


def cache_covers(last, end: str | None = None) -> bool:
    if last is None:
        return False
    try:
        if pd.isna(last):
            return False
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(last).strftime("%Y%m%d") >= official_end_key(end)


def disable_http_proxy() -> None:
    import os
    import urllib.request

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    urllib.request.getproxies = lambda: {}  # type: ignore[method-assign]
    urllib.request.getproxies_environment = lambda: {}  # type: ignore[method-assign]
    if hasattr(urllib.request, "getproxies_registry"):
        urllib.request.getproxies_registry = lambda: {}  # type: ignore[method-assign]


def _ymd(yyyymmdd: str) -> str:
    s = yyyymmdd.replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def to_bao(ts_code: str) -> str:
    num, mkt = ts_code.split(".")
    return f"{mkt.lower()}.{num}"


def to_ts(bao_code: str) -> str:
    mkt, num = bao_code.split(".")
    return f"{num}.{mkt.upper()}"


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(
        columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }
    )
    missing = [c for c in ("date", "open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"日K缺列: {missing}；实际列={list(raw.columns)}")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def _clip(df: pd.DataFrame, end: str | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    cap = pd.Timestamp(official_end_key(end))
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out[out["date"] <= cap]
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_constituents(*, force: bool = False) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not force and MEMBERS_FILE.exists():
        return pd.read_csv(MEMBERS_FILE)
    disable_http_proxy()
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(lg.error_msg)
    try:
        rs = bs.query_hs300_stocks()
        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        raw = pd.DataFrame(rows, columns=rs.fields)
    finally:
        bs.logout()
    out = pd.DataFrame(
        {
            "ts_code": [to_ts(c) for c in raw["code"]],
            "name": raw["code_name"],
            "bao_code": raw["code"],
        }
    )
    out.to_csv(MEMBERS_FILE, index=False, encoding="utf-8-sig")
    print(f"成分 {len(out)} 只  {MEMBERS_FILE}")
    return out


def fetch_hs300(start: str = PRICE_START, end: str | None = None, *, force: bool = False) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    start_key = start.replace("-", "")
    end_key = (end or date.today().strftime("%Y%m%d")).replace("-", "")
    fetch_end = official_end_key(end_key)
    if not force and INDEX_FILE.exists():
        cached = pd.read_csv(INDEX_FILE, parse_dates=["date"])
        if not cached.empty and cache_covers(cached["date"].max(), end_key):
            return cached[
                (cached["date"] >= pd.Timestamp(start_key)) & (cached["date"] <= pd.Timestamp(end_key))
            ].reset_index(drop=True)
    disable_http_proxy()
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(lg.error_msg)
    try:
        rs = bs.query_history_k_data_plus(
            "sh.000300",
            "date,open,high,low,close,volume",
            start_date=_ymd(start_key),
            end_date=_ymd(fetch_end),
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        raw = pd.DataFrame(rows, columns=rs.fields)
    finally:
        bs.logout()
    df = _clip(_normalize(raw), end_key)
    df.to_csv(INDEX_FILE, index=False, encoding="utf-8-sig")
    print(f"沪深300  {df['date'].min().date()} ~ {df['date'].max().date()}  {INDEX_FILE}")
    return df[(df["date"] >= pd.Timestamp(start_key)) & (df["date"] <= pd.Timestamp(end_key))].reset_index(drop=True)


def fetch_klines(
    codes: list[str], start: str = PRICE_START, end: str | None = None, *, force: bool = False
) -> dict[str, pd.DataFrame]:
    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    start_key = start.replace("-", "")
    end_key = (end or date.today().strftime("%Y%m%d")).replace("-", "")
    fetch_end = official_end_key(end_key)
    out: dict[str, pd.DataFrame] = {}
    need: list[str] = []
    for code in codes:
        path = STOCK_DIR / f"{code.replace('.', '_')}.csv"
        if not force and path.exists():
            cached = pd.read_csv(path, parse_dates=["date"])
            if not cached.empty and cache_covers(cached["date"].max(), end_key):
                out[code] = cached[
                    (cached["date"] >= pd.Timestamp(start_key)) & (cached["date"] <= pd.Timestamp(end_key))
                ].reset_index(drop=True)
                continue
        need.append(code)
    if not need:
        print(f"个股日K已齐 {len(out)} 只")
        return out

    disable_http_proxy()
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(lg.error_msg)
    print(f"下载个股日K {len(need)} 只（前复权）…")
    try:
        for i, code in enumerate(need, start=1):
            rs = bs.query_history_k_data_plus(
                to_bao(code),
                "date,open,high,low,close,volume",
                start_date=_ymd(start_key),
                end_date=_ymd(fetch_end),
                frequency="d",
                adjustflag="2",
            )
            if rs.error_code != "0":
                print(f"  {code} 失败：{rs.error_msg}")
                continue
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue
            df = _clip(_normalize(pd.DataFrame(rows, columns=rs.fields)), end_key)
            path = STOCK_DIR / f"{code.replace('.', '_')}.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            out[code] = df[
                (df["date"] >= pd.Timestamp(start_key)) & (df["date"] <= pd.Timestamp(end_key))
            ].reset_index(drop=True)
            if i % 10 == 0 or i == len(need):
                print(f"  K线 {i}/{len(need)}", flush=True)
    finally:
        bs.logout()
    return out


def assemble_universe(hs300: pd.DataFrame, bags: dict[str, pd.DataFrame], names: dict[str, str], *, source: str) -> dict:
    hs300 = hs300.copy()
    hs300["date"] = pd.to_datetime(hs300["date"])
    cal = pd.DatetimeIndex(hs300["date"].sort_values().unique())

    def panel(col: str) -> pd.DataFrame:
        parts = {}
        for code, frame in bags.items():
            work = frame.copy()
            work["date"] = pd.to_datetime(work["date"])
            parts[code] = work.set_index("date")[col].astype(float)
        return pd.DataFrame(parts).reindex(cal)

    idx = hs300.drop_duplicates("date").set_index("date")
    return {
        "cal": cal,
        "open": panel("open"),
        "close": panel("close"),
        "idx_open": idx["open"].astype(float).reindex(cal),
        "idx_close": idx["close"].astype(float).reindex(cal),
        "n_stocks": len(bags),
        "names": names,
        "source": source,
    }


def local_csv_ready() -> bool:
    if not INDEX_FILE.exists() or not MEMBERS_FILE.exists():
        return False
    n = len(list(STOCK_DIR.glob("*.csv"))) if STOCK_DIR.exists() else 0
    return n >= 200


def load_from_csv() -> dict:
    members = pd.read_csv(MEMBERS_FILE)
    hs300 = pd.read_csv(INDEX_FILE, parse_dates=["date"])
    names = {str(r["ts_code"]): str(r["name"]) for _, r in members.iterrows()}
    bags: dict[str, pd.DataFrame] = {}
    for _, row in members.iterrows():
        code = str(row["ts_code"])
        path = STOCK_DIR / f"{code.replace('.', '_')}.csv"
        if not path.exists():
            continue
        bags[code] = pd.read_csv(path, parse_dates=["date"])
    if len(bags) < 50:
        raise RuntimeError(f"本仓库个股日K太少（{len(bags)}），请运行 python run_download.py")
    return assemble_universe(hs300, bags, names, source=str(DATA_DIR))


def download_prices(*, force: bool = False) -> dict:
    members = fetch_constituents(force=force)
    hs300 = fetch_hs300(force=force)
    codes = [str(c) for c in members["ts_code"].tolist()]
    names = {str(r["ts_code"]): str(r["name"]) for _, r in members.iterrows()}
    bags = fetch_klines(codes, force=force)
    return assemble_universe(hs300, bags, names, source=f"BaoStock {DATA_DIR}")
