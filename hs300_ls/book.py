"""T+1 开盘成交的净多头动量账本。印花税 + 融券费。弱市半仓。"""

from __future__ import annotations

import json
import math
from datetime import date

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator

from hs300_ls.config import (
    BORROW_ANNUAL,
    COMMISSION,
    EXECUTION_NOTE,
    FULL_START,
    INDEX_MA,
    LONG_GROSS,
    MOM_LOOKBACK,
    MOM_SKIP,
    OUTPUT_DIR,
    REBALANCE_BARS,
    REPORT_START,
    RISK_OFF_SCALE,
    SCHEME_ID,
    SCHEME_NAME,
    SHORT_GROSS,
    SHORT_N,
    SLIPPAGE_BUY,
    SLIPPAGE_SELL,
    STAMP_TAX,
    TEST_START,
    TOP_N,
    TRAIN_END,
    TRAIN_START,
)
from hs300_ls.formula import build_weights, index_risk_on, momentum_score
from hs300_ls.prices import load_prices

STEM = "book"


def last_target(
    weights: pd.DataFrame,
    risk_on: pd.Series,
    names: dict | None = None,
    score: pd.DataFrame | None = None,
    *,
    rebalance_bars: int = REBALANCE_BARS,
    index_ma: int = INDEX_MA,
) -> dict:
    """最近一次换仓锁定的目标仓位。T 收盘信号，T+1 开盘成交。不是买卖建议。"""
    names = names or {}
    cal = pd.DatetimeIndex(weights.index)
    n = len(cal)
    if n == 0:
        return {"ok": False, "error": "没有权重"}
    step = max(1, int(rebalance_bars))
    last_i = n - 1
    sig_i = (last_i // step) * step
    fill_i = sig_i + 1
    next_i = sig_i + step
    w = weights.iloc[last_i].astype(float)
    longs = w[w > 1e-12].sort_values(ascending=False)
    shorts = w[w < -1e-12].sort_values(ascending=True)
    sc_row = None
    if score is not None and len(score.index):
        sc_row = score.reindex(cal).iloc[sig_i]

    def _legs(series: pd.Series, side: str) -> list[dict]:
        rows = []
        for code, wt in series.items():
            code_s = str(code)
            mom = None
            if sc_row is not None and code in sc_row.index:
                raw = sc_row[code]
                if pd.notna(raw) and math.isfinite(float(raw)):
                    mom = float(raw)
            rows.append(
                {
                    "code": code_s,
                    "name": str(names.get(code_s) or names.get(code) or code_s),
                    "side": side,
                    "weight": float(wt),
                    "score": mom,
                }
            )
        return rows

    on_sig = bool(risk_on.reindex(cal).fillna(False).iloc[sig_i])
    on_asof = bool(risk_on.reindex(cal).fillna(False).iloc[last_i])
    long_sum = float(longs.sum()) if len(longs) else 0.0
    short_sum = float((-shorts).sum()) if len(shorts) else 0.0
    fill_in_sample = fill_i < n
    return {
        "ok": True,
        "asof": cal[last_i].strftime("%Y-%m-%d"),
        "signal_date": cal[sig_i].strftime("%Y-%m-%d"),
        "fill_date": cal[fill_i].strftime("%Y-%m-%d") if fill_in_sample else None,
        "fill_pending": not fill_in_sample,
        "next_rebalance_date": cal[next_i].strftime("%Y-%m-%d") if next_i < n else None,
        "bars_since_signal": int(last_i - sig_i),
        "bars_to_next": int(step - (last_i - sig_i)),
        "rebalance_bars": step,
        "index_ma": int(index_ma),
        "risk_on_at_signal": on_sig,
        "risk_on_asof": on_asof,
        "risk_off": not on_sig,
        "long_gross": long_sum,
        "short_gross": short_sum,
        "net_gross": long_sum - short_sum,
        "n_long": int(len(longs)),
        "n_short": int(len(shorts)),
        "longs": _legs(longs, "long"),
        "shorts": _legs(shorts, "short"),
        "note": (
            "T 日收盘出信号，T+1 日开盘成交。下面是最近一次换仓锁定的目标仓位，用来核对公式，不是当日买卖建议。"
            "空头按融券假设，不是大A可直接做空。当前成分名单有幸存者偏差。"
        ),
    }


def session_parts(open_px: pd.DataFrame, close_px: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    overnight = open_px / close_px.shift(1) - 1
    intraday = close_px / open_px - 1
    return overnight, intraday


def signed_portfolio(
    w_sig: pd.DataFrame,
    open_px: pd.DataFrame,
    close_px: pd.DataFrame,
    idx_open: pd.Series,
    idx_close: pd.Series,
) -> pd.DataFrame:
    cal = close_px.index
    w_sig = w_sig.reindex(cal).fillna(0.0)
    open_px = open_px.reindex(cal)
    close_px = close_px.reindex(cal)
    overnight, intraday = session_parts(open_px, close_px)
    overnight = overnight.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    intraday = intraday.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    valid = open_px.notna() & close_px.notna() & close_px.shift(1).notna() & (open_px > 0)
    w_sig = w_sig.where(valid, 0.0)

    w_intraday = w_sig.shift(1).fillna(0.0)
    w_overnight = w_sig.shift(2).fillna(0.0)
    gross = (w_overnight * overnight).sum(axis=1) + (w_intraday * intraday).sum(axis=1)
    delta = w_intraday - w_overnight
    buy = delta.clip(lower=0.0).sum(axis=1)
    sell = (-delta.clip(upper=0.0)).sum(axis=1)
    short_expo = (-w_intraday.clip(upper=0.0)).sum(axis=1)
    borrow = short_expo * (BORROW_ANNUAL / 252.0)
    cost = buy * (COMMISSION + SLIPPAGE_BUY) + sell * (COMMISSION + SLIPPAGE_SELL + STAMP_TAX) + borrow
    net = gross - cost

    idx_close = idx_close.reindex(cal)
    idx_cc = (idx_close / idx_close.shift(1) - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    held = w_intraday > 1e-12
    shorted = w_intraday < -1e-12
    out = pd.DataFrame(
        {
            "gross_ret": gross,
            "net_ret": net,
            "cost": cost,
            "borrow": borrow,
            "turnover": buy + sell,
            "n_hold": held.sum(axis=1),
            "n_short": shorted.sum(axis=1),
            "long_expo": w_intraday.clip(lower=0.0).sum(axis=1),
            "short_expo": short_expo,
            "hs300_ret": idx_cc,
        },
        index=cal,
    )
    out["net_vs_hs300"] = out["net_ret"] - out["hs300_ret"]
    return out


def _zero_first(work: pd.DataFrame) -> pd.DataFrame:
    out = work.copy()
    for col in ("gross_ret", "net_ret", "cost", "hs300_ret", "net_vs_hs300"):
        if col in out.columns and len(out):
            out.iloc[0, out.columns.get_loc(col)] = 0.0
    return out


def _ttest(x: pd.Series) -> tuple[float, float]:
    v = pd.to_numeric(x, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    n = int(len(v))
    if n < 5 or float(v.std(ddof=1) or 0) == 0:
        return float("nan"), float("nan")
    t = float(v.mean() / (v.std(ddof=1) / math.sqrt(n)))
    p = 2.0 * (0.5 * math.erfc(abs(t) / math.sqrt(2.0)))
    return t, p


def window_metrics(daily: pd.DataFrame, start: str, end: str | None, label: str) -> dict:
    work = daily.copy()
    work.index = pd.DatetimeIndex(work.index)
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end) if end else work.index.max()
    work = work[(work.index >= lo) & (work.index <= hi)]
    if len(work) < 5:
        return {"sample": label, "n": 0}
    work = _zero_first(work)
    r = work["net_ret"].astype(float).fillna(0.0)
    g = work["gross_ret"].astype(float).fillna(0.0)
    nav = (1 + r).cumprod()
    bench = (1 + work["hs300_ret"].astype(float).fillna(0.0)).cumprod()
    n = len(work)
    years = n / 252.0
    total = float(nav.iloc[-1] / nav.iloc[0] - 1)
    total_g = float((1 + g).cumprod().iloc[-1] / (1 + g).cumprod().iloc[0] - 1)
    bh = float(bench.iloc[-1] / bench.iloc[0] - 1)
    dd = float((nav / nav.cummax() - 1).min())
    x = work["net_vs_hs300"].astype(float).fillna(0.0)
    t_x, p_x = _ttest(x.iloc[1:])
    live = (work["long_expo"] + work["short_expo"]) > 1e-8
    gross_expo = work["long_expo"] + work["short_expo"]
    return {
        "sample": label,
        "start": work.index.min().strftime("%Y-%m-%d"),
        "end": work.index.max().strftime("%Y-%m-%d"),
        "n": n,
        "years": years,
        "gross_return": total_g,
        "net_return": total,
        "hs300_return": bh,
        "excess_additive": total - bh,
        "max_drawdown": dd,
        "avg_turnover": float(work["turnover"].mean()),
        "avg_hold": float(work["n_hold"].mean()),
        "avg_short": float(work["n_short"].mean()),
        "avg_long_expo": float(work["long_expo"].mean()),
        "avg_short_expo": float(work["short_expo"].mean()),
        "avg_net_expo": float((work["long_expo"] - work["short_expo"]).mean()),
        "cash_day_share": float((~live).mean()),
        "half_size_share": float(((gross_expo > 0.15) & (gross_expo < 0.65)).mean()),
        "under_index_share": float((nav < bench).mean()),
        "cost_drag": total_g - total,
        "t_vs_hs300": t_x,
        "p_vs_hs300": p_x,
        "sharpe_net": float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if float(r.std(ddof=1) or 0) else float("nan"),
    }


def sample_from_daily(daily: pd.DataFrame, start: str, end: str | None) -> pd.DataFrame:
    work = daily.copy()
    work.index = pd.DatetimeIndex(work.index)
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end) if end else work.index.max()
    work = work[(work.index >= lo) & (work.index <= hi)].copy()
    if work.empty:
        raise RuntimeError("报告窗口为空。")
    work = _zero_first(work)
    work = work.reset_index(names="date")
    work["date"] = pd.to_datetime(work["date"])
    work["strategy_ret"] = work["net_ret"]
    work["benchmark_ret"] = work["hs300_ret"]
    work["nav"] = (1 + work["strategy_ret"]).cumprod()
    work["bench"] = (1 + work["benchmark_ret"]).cumprod()
    work["excess_nav"] = work["nav"] / work["bench"]
    return work


def period_rows(sample: pd.DataFrame) -> list[dict]:
    d0 = pd.Timestamp(sample["date"].iloc[0])
    d1 = pd.Timestamp(sample["date"].iloc[-1])
    windows = [
        ("full", "全样本", d0, d1),
        ("y2425", "2024-09～2025-12", pd.Timestamp("2024-09-02"), pd.Timestamp("2025-12-31")),
        ("y24", "2024-09～2024-12", pd.Timestamp("2024-09-02"), pd.Timestamp("2024-12-31")),
        ("y25", "2025全年", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        ("y26", "2026至今", pd.Timestamp("2026-01-01"), d1),
    ]
    rows = []
    for pid, label, a, b in windows:
        w = sample[(sample["date"] >= a) & (sample["date"] <= b)]
        if len(w) < 2:
            continue
        nav = (1 + w["strategy_ret"].astype(float)).cumprod()
        bench = (1 + w["benchmark_ret"].astype(float)).cumprod()
        tot = float(nav.iloc[-1] / nav.iloc[0] - 1)
        bh = float(bench.iloc[-1] / bench.iloc[0] - 1)
        rows.append(
            {
                "id": pid,
                "label": label,
                "start": pd.Timestamp(w["date"].iloc[0]).strftime("%Y-%m-%d"),
                "end": pd.Timestamp(w["date"].iloc[-1]).strftime("%Y-%m-%d"),
                "strategy": tot,
                "hs300": bh,
                "excess_additive": tot - bh,
                "max_drawdown": float((nav / nav.cummax() - 1).min()),
            }
        )
    return rows


def monthly(sample: pd.DataFrame) -> pd.DataFrame:
    eq = sample.set_index("date")[["nav", "bench"]]
    eq.index = pd.to_datetime(eq.index)
    try:
        last_s = eq["nav"].resample("ME").last()
        last_b = eq["bench"].resample("ME").last()
        first_s = eq["nav"].resample("ME").first()
        first_b = eq["bench"].resample("ME").first()
    except ValueError:
        last_s = eq["nav"].resample("M").last()
        last_b = eq["bench"].resample("M").last()
        first_s = eq["nav"].resample("M").first()
        first_b = eq["bench"].resample("M").first()
    s = last_s.pct_change()
    b = last_b.pct_change()
    s.iloc[0] = last_s.iloc[0] / first_s.iloc[0] - 1
    b.iloc[0] = last_b.iloc[0] / first_b.iloc[0] - 1
    return pd.DataFrame(
        {
            "month": last_s.index.strftime("%Y-%m"),
            "strategy": s.to_numpy(),
            "benchmark": b.to_numpy(),
            "excess": (s - b).to_numpy(),
        }
    ).reset_index(drop=True)


def _metrics(sample: pd.DataFrame, n_stocks: int, windows: dict) -> dict:
    n = len(sample)
    years = n / 252 if n else 0
    nav = sample["nav"].astype(float)
    bench = sample["bench"].astype(float)
    sret = sample["strategy_ret"].astype(float)
    total = float(nav.iloc[-1] / nav.iloc[0] - 1)
    bh = float(bench.iloc[-1] / bench.iloc[0] - 1)
    live = (sample["long_expo"] + sample["short_expo"]) > 1e-8
    gross_expo = sample["long_expo"] + sample["short_expo"]
    xret = sample["strategy_ret"].astype(float) - sample["benchmark_ret"].astype(float)
    month_df = monthly(sample)
    return {
        "product": SCHEME_NAME,
        "scheme": SCHEME_ID,
        "scheme_name": SCHEME_NAME,
        "start": sample["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": sample["date"].iloc[-1].strftime("%Y-%m-%d"),
        "days": n,
        "n_stocks": n_stocks,
        "total_return": total,
        "annual_return": (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0,
        "benchmark_return": bh,
        "excess_additive": total - bh,
        "sharpe": float(sret.mean() / sret.std() * (252 ** 0.5)) if float(sret.std() or 0) else 0.0,
        "information_ratio": float(xret.mean() / xret.std() * (252 ** 0.5)) if float(xret.std() or 0) else 0.0,
        "max_drawdown": float((nav / nav.cummax() - 1).min()),
        "benchmark_drawdown": float((bench / bench.cummax() - 1).min()),
        "avg_holdings_when_active": float(sample.loc[live, "n_hold"].mean()) if live.any() else 0.0,
        "avg_shorts_when_active": float(sample.loc[live, "n_short"].mean()) if live.any() else 0.0,
        "avg_long_expo": float(sample["long_expo"].mean()),
        "avg_short_expo": float(sample["short_expo"].mean()),
        "avg_net_expo": float((sample["long_expo"] - sample["short_expo"]).mean()),
        "cash_day_share": float((~live).mean()),
        "half_size_share": float(((gross_expo > 0.15) & (gross_expo < 0.65)).mean()),
        "under_index_share": float((nav < bench).mean()),
        "month_excess_win": float((month_df["excess"] > 0).mean()) if len(month_df) else 0.0,
        "avg_turnover": float(sample["turnover"].mean()),
        "execution": EXECUTION_NOTE,
        "rules": (
            f"从方案三动量公式拎出，规则事先固定，不对报告窗口搜参。"
            f"{MOM_LOOKBACK} 日动量跳过 {MOM_SKIP} 日；做多最高 {TOP_N} 只、做空最低 {SHORT_N} 只。"
            f"目标仓位多头 {LONG_GROSS:.0%}、空头 {SHORT_GROSS:.0%}（总敞口约 100%，净敞口约 {LONG_GROSS - SHORT_GROSS:.0%}）。"
            f"沪深300收盘低于 {INDEX_MA} 日均线时仓位 ×{RISK_OFF_SCALE:.0%}，不空仓。"
            f"每 {REBALANCE_BARS} 个交易日换仓。T+1 开盘。"
            f"印花税 {STAMP_TAX:.2%}，空头年化融券 {BORROW_ANNUAL:.0%}。"
        ),
        "note": (
            "主看相对现金的净值和夏普，以及相对沪深300的累计差 / 信息比率。"
            "净多头约 40%，牛市里仍可能一段时间低于满仓指数。"
            "当前成分名单有幸存者偏差。不能和项目一的 4% 超额直接比。"
        ),
        "periods": period_rows(sample),
        "samples": windows,
        "frozen": {
            "top_n": TOP_N,
            "short_n": SHORT_N,
            "long_gross": LONG_GROSS,
            "short_gross": SHORT_GROSS,
            "risk_off_scale": RISK_OFF_SCALE,
            "mom_lookback": MOM_LOOKBACK,
            "mom_skip": MOM_SKIP,
            "index_ma": INDEX_MA,
            "rebalance_bars": REBALANCE_BARS,
            "stamp_tax": STAMP_TAX,
            "borrow_annual": BORROW_ANNUAL,
            "tuned_on_report_window": False,
        },
    }


def plot_book(sample: pd.DataFrame, month_df: pd.DataFrame, metrics: dict, path) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 1, figsize=(12.8, 17.6), gridspec_kw={"height_ratios": [1.2, 2.15, 1.25]})
    d = pd.to_datetime(sample["date"])
    nav = sample["nav"].astype(float)
    bench = sample["bench"].astype(float)

    ax = axes[0]
    ax.plot(d, nav, color="#1f6feb", lw=2.0, label="账本")
    ax.plot(d, bench, color="#8b949e", lw=1.6, label="沪深300")
    ax.axhline(1.0, color="#d0d7de", lw=1.0)
    ax.set_title(
        f"{metrics['scheme_name']}    {metrics['start']} ~ {metrics['end']}    "
        f"净值 {metrics['total_return']:.2%}    指数 {metrics['benchmark_return']:.2%}",
        fontsize=13,
        pad=10,
    )
    ax.set_ylabel("净值（期初=1）", fontsize=11)
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(True, alpha=0.28)
    ax.yaxis.set_major_locator(MaxNLocator(8))

    ax2 = axes[1]
    xs = nav / bench
    ax2.plot(d, xs, color="#1f6feb", lw=2.0, label="账本 / 沪深300")
    ax2.axhline(1.0, color="#8b949e", lw=1.0)
    ax2.set_ylabel("相对指数（>1 表示累计超过沪深300）", fontsize=11)
    ax2.legend(loc="upper left", fontsize=10)
    ax2.grid(True, alpha=0.32)
    ax2.yaxis.set_major_locator(MaxNLocator(10))

    ax3 = axes[2]
    month_x = pd.to_datetime(month_df["month"] + "-01")
    month_y = month_df["excess"].astype(float) * 100.0
    colors = np.where(month_y.to_numpy() >= 0, "#e74c3c", "#1abc9c")
    ax3.bar(month_x, month_y, width=22, color=colors, alpha=0.9, linewidth=0)
    ax3.axhline(0, color="#8b949e", lw=1.0)
    ax3.set_ylabel("相对沪深300月超额（%）", fontsize=11)
    ax3.set_xlabel("日期", fontsize=11)
    ax3.grid(True, axis="y", alpha=0.32)
    fig.tight_layout(h_pad=1.35)
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def run_backtest() -> dict:
    uni = load_prices()
    cal = pd.DatetimeIndex(uni["cal"])
    open_px = uni["open"].reindex(cal)
    close_px = uni["close"].reindex(cal)
    idx_open = uni["idx_open"].reindex(cal)
    idx_close = uni["idx_close"].reindex(cal)
    valid = open_px.notna() & close_px.notna() & close_px.shift(1).notna() & (open_px > 0)
    score = momentum_score(close_px)
    risk_on = index_risk_on(idx_close)
    weights = build_weights(score, valid, risk_on)
    daily = signed_portfolio(weights, open_px, close_px, idx_open, idx_close)
    last = cal.max().strftime("%Y-%m-%d")
    windows = {
        "train": window_metrics(daily, TRAIN_START, TRAIN_END, "train"),
        "test": window_metrics(daily, TEST_START, last, "test"),
        "report": window_metrics(daily, REPORT_START, last, "report"),
        "full": window_metrics(daily, FULL_START, last, "full"),
    }
    sample = sample_from_daily(daily, REPORT_START, last)
    month_df = monthly(sample)
    n_stocks = int(uni.get("n_stocks") or open_px.shape[1])
    metrics = _metrics(sample, n_stocks, windows)
    metrics["updated"] = date.today().isoformat()
    metrics["price_source"] = uni.get("source")
    holdings = last_target(weights, risk_on, uni.get("names") or {}, score)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{STEM}_holdings.json").write_text(
        json.dumps(holdings, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    sample.to_csv(OUTPUT_DIR / f"{STEM}_equity.csv", index=False, encoding="utf-8-sig")
    month_df.to_csv(OUTPUT_DIR / f"{STEM}_monthly.csv", index=False, encoding="utf-8-sig")
    (OUTPUT_DIR / f"{STEM}_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    plot_book(sample, month_df, metrics, OUTPUT_DIR / f"{STEM}.png")
    print(f"已写入 {OUTPUT_DIR / STEM}_*")
    for key in ("train", "test", "report"):
        row = windows[key]
        print(
            f"{key:8s}  {row.get('start')}~{row.get('end')}  "
            f"nav={row.get('net_return'):+.2%}  vs300={row.get('excess_additive'):+.2%}  "
            f"under={row.get('under_index_share'):.1%}  half={row.get('half_size_share'):.1%}  "
            f"dd={row.get('max_drawdown'):+.2%}"
        )
    return metrics
