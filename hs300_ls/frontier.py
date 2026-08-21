"""仓位结构对照：同一公式、同一成交与成本，只改多空和弱市杠杆。

不对报告窗口搜参。用于展示相对超额与回撤的反向关系。
"""

from __future__ import annotations

import json
import math
from datetime import date

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from hs300_ls.book import (
    _metrics,
    last_target,
    monthly,
    plot_book,
    sample_from_daily,
    signed_portfolio,
    window_metrics,
)
from hs300_ls.config import (
    BORROW_ANNUAL,
    INDEX_MA,
    MOM_LOOKBACK,
    MOM_SKIP,
    OUTPUT_DIR,
    REBALANCE_BARS,
    REPORT_START,
    SHORT_N,
    STAMP_TAX,
    TEST_START,
    TOP_N,
    TRAIN_END,
    TRAIN_START,
)
from hs300_ls.formula import build_weights, index_risk_on, momentum_score
from hs300_ls.prices import load_prices

VAR_DIR = OUTPUT_DIR / "variants"

VARIANTS = [
    {
        "id": "now",
        "name": "当前：70/30 弱市半仓",
        "short": "70/30 半仓",
        "long_gross": 0.70,
        "short_gross": 0.30,
        "risk_off_scale": 0.50,
        "current": True,
    },
    {
        "id": "cash",
        "name": "70/30 弱市空仓",
        "short": "70/30 空仓",
        "long_gross": 0.70,
        "short_gross": 0.30,
        "risk_off_scale": 0.0,
        "current": False,
    },
    {
        "id": "x75",
        "name": "70/30 弱市 75 折",
        "short": "70/30 75折",
        "long_gross": 0.70,
        "short_gross": 0.30,
        "risk_off_scale": 0.75,
        "current": False,
    },
    {
        "id": "n85",
        "name": "85/15 弱市半仓",
        "short": "85/15 半仓",
        "long_gross": 0.85,
        "short_gross": 0.15,
        "risk_off_scale": 0.50,
        "current": False,
    },
    {
        "id": "n85_cash",
        "name": "85/15 弱市空仓",
        "short": "85/15 空仓",
        "long_gross": 0.85,
        "short_gross": 0.15,
        "risk_off_scale": 0.0,
        "current": False,
    },
    {
        "id": "long_half",
        "name": "只做多、弱市半仓",
        "short": "只做多 半仓",
        "long_gross": 1.00,
        "short_gross": 0.00,
        "risk_off_scale": 0.50,
        "current": False,
    },
    {
        "id": "long_cash",
        "name": "只做多、弱市空仓",
        "short": "只做多 空仓",
        "long_gross": 1.00,
        "short_gross": 0.00,
        "risk_off_scale": 0.0,
        "current": False,
    },
    {
        "id": "long_full",
        "name": "只做多、满仓不降",
        "short": "只做多 满仓",
        "long_gross": 1.00,
        "short_gross": 0.00,
        "risk_off_scale": 1.00,
        "current": False,
    },
]


def _rel(row: dict) -> float:
    nav = 1.0 + float(row["net_return"])
    bh = 1.0 + float(row["hs300_return"])
    if bh <= 0:
        return float("nan")
    return nav / bh - 1.0


def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (np.floating, float)):
        v = float(v)
        return v if math.isfinite(v) else None
    if isinstance(v, (np.integer, int)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item") and not isinstance(v, (bytes, str, dict, list, tuple)):
        try:
            return _jsonable(v.item())
        except (ValueError, AttributeError):
            pass
    return v


def _windows(daily: pd.DataFrame, last: str) -> dict:
    return {
        "train": window_metrics(daily, TRAIN_START, TRAIN_END, "train"),
        "test": window_metrics(daily, TEST_START, last, "test"),
        "report": window_metrics(daily, REPORT_START, last, "report"),
    }


def _patch_metrics(metrics: dict, spec: dict) -> dict:
    lg = float(spec["long_gross"])
    sg = float(spec["short_gross"])
    rs = float(spec["risk_off_scale"])
    short_part = (
        f"做空最低 {SHORT_N} 只合计约 {sg:.0%}。"
        if sg > 0
        else "不做空。"
    )
    if rs <= 0:
        risk_part = f"沪深300收盘低于 {INDEX_MA} 日均线时空仓。"
    elif rs < 1:
        risk_part = f"沪深300收盘低于 {INDEX_MA} 日均线时仓位 ×{rs:.0%}，不空仓。"
    else:
        risk_part = f"沪深300低于 {INDEX_MA} 日均线时也不降仓。"
    metrics["scheme"] = spec["id"]
    metrics["scheme_name"] = spec["name"]
    metrics["product"] = spec["name"]
    metrics["frozen"]["long_gross"] = lg
    metrics["frozen"]["short_gross"] = sg
    metrics["frozen"]["risk_off_scale"] = rs
    metrics["rules"] = (
        f"从方案三动量公式拎出，规则事先固定，不对报告窗口搜参。"
        f"{MOM_LOOKBACK} 日动量跳过 {MOM_SKIP} 日；做多最高 {TOP_N} 只。"
        f"{short_part}"
        f"目标仓位多头 {lg:.0%}、空头 {sg:.0%}（总敞口约 {lg + sg:.0%}，净敞口约 {lg - sg:.0%}）。"
        f"{risk_part}"
        f"每 {REBALANCE_BARS} 个交易日换仓。T+1 开盘。"
        f"印花税 {STAMP_TAX:.2%}，空头年化融券 {BORROW_ANNUAL:.0%}。"
    )
    net = lg - sg
    metrics["note"] = (
        "主看相对现金的净值和夏普，以及相对沪深300的累计差 / 信息比率。"
        f"净多头约 {net:.0%}，牛市里仍可能一段时间低于满仓指数。"
        "当前成分名单有幸存者偏差。不能和项目一的 4% 超额直接比。"
    )
    return metrics


def _monthly_records(month_df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in month_df.iterrows():
        rows.append(
            {
                "month": str(row["month"]),
                "strategy": _jsonable(row["strategy"]),
                "benchmark": _jsonable(row["benchmark"]),
                "excess": _jsonable(row["excess"]),
            }
        )
    return rows


def write_target(spec: dict, weights: pd.DataFrame, risk_on: pd.Series, names: dict, score: pd.DataFrame) -> dict:
    payload = last_target(
        weights,
        risk_on,
        names,
        score,
        long_gross=spec["long_gross"],
        short_gross=spec["short_gross"],
        allow_short=spec["short_gross"] > 0,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    path = VAR_DIR / f"{spec['id']}_holdings.json"
    text = json.dumps(_jsonable(payload), ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    if spec.get("current"):
        (OUTPUT_DIR / "book_holdings.json").write_text(text, encoding="utf-8")
    return payload


def _scheme_pack(
    spec: dict,
    daily: pd.DataFrame,
    last: str,
    n_stocks: int,
    windows: dict,
    holdings: dict | None = None,
) -> dict:
    sample = sample_from_daily(daily, REPORT_START, last)
    month_df = monthly(sample)
    metrics = _patch_metrics(_metrics(sample, n_stocks, windows), spec)
    metrics["updated"] = date.today().isoformat()
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    png = VAR_DIR / f"{spec['id']}.png"
    plot_book(sample, month_df, metrics, png)
    (VAR_DIR / f"{spec['id']}_metrics.json").write_text(
        json.dumps(_jsonable(metrics), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    month_df.to_csv(VAR_DIR / f"{spec['id']}_monthly.csv", index=False, encoding="utf-8-sig")
    pack = {
        "ok": True,
        "id": spec["id"],
        "metrics": _jsonable(metrics),
        "monthly": _monthly_records(month_df),
        "chart": f"/variants/{spec['id']}.png",
        "has_chart": png.exists(),
    }
    if holdings:
        pack["holdings"] = _jsonable(holdings)
    return pack


def _row(spec: dict, windows: dict) -> dict:
    tr = windows["train"]
    te = windows["test"]
    rp = windows["report"]
    return {
        "id": spec["id"],
        "name": spec["name"],
        "short": spec["short"],
        "current": spec["current"],
        "long_gross": spec["long_gross"],
        "short_gross": spec["short_gross"],
        "risk_off_scale": spec["risk_off_scale"],
        "report_nav": rp.get("net_return"),
        "report_hs300": rp.get("hs300_return"),
        "report_excess_additive": rp.get("excess_additive"),
        "report_excess_relative": _rel(rp),
        "report_dd": rp.get("max_drawdown"),
        "report_sharpe": rp.get("sharpe_net"),
        "train_nav": tr.get("net_return"),
        "train_dd": tr.get("max_drawdown"),
        "test_nav": te.get("net_return"),
        "test_dd": te.get("max_drawdown"),
    }


def plot_frontier(rows: list[dict], path) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.6))

    xs = np.array([r["report_excess_relative"] * 100 for r in rows], dtype=float)
    ys = np.array([r["report_dd"] * 100 for r in rows], dtype=float)
    ax = axes[0]
    order = np.argsort(xs)
    ax.plot(xs[order], ys[order], color="#8b949e", lw=1.6, zorder=1)
    for r, x, y in zip(rows, xs, ys):
        color = "#1f6feb" if r["current"] else "#e24c4c"
        ax.scatter([x], [y], s=90 if r["current"] else 64, color=color, zorder=2)
        ax.annotate(
            r["short"],
            (x, y),
            textcoords="offset points",
            xytext=(8, 6),
            fontsize=9,
            color="#24292f",
        )
    ax.set_title("报告窗口：相对超额越高，回撤越深", fontsize=12, pad=8)
    ax.set_xlabel("相对超额（%）  = 账本净值 / 沪深300净值 − 1", fontsize=10)
    ax.set_ylabel("报告窗口最大回撤（%）", fontsize=10)
    ax.grid(True, alpha=0.32)
    ax.axhline(0, color="#d0d7de", lw=1.0)

    ax2 = axes[1]
    labels = [r["short"] for r in rows]
    idx = np.arange(len(rows))
    w = 0.36
    rel = [r["report_excess_relative"] * 100 for r in rows]
    dd = [abs(r["report_dd"]) * 100 for r in rows]
    ax2.bar(idx - w / 2, rel, w, color="#e24c4c", label="相对超额（%）")
    ax2.bar(idx + w / 2, dd, w, color="#1abc9c", label="报告窗口回撤绝对值（%）")
    ax2.set_xticks(idx)
    ax2.set_xticklabels(labels, fontsize=8, rotation=18, ha="right")
    ax2.set_title("同一方向：超额柱升高，回撤柱也升高", fontsize=12, pad=8)
    ax2.set_ylabel("百分比", fontsize=10)
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(True, axis="y", alpha=0.32)

    fig.tight_layout(w_pad=2.0)
    fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)


def run_frontier() -> dict:
    uni = load_prices()
    cal = pd.DatetimeIndex(uni["cal"])
    open_px = uni["open"].reindex(cal)
    close_px = uni["close"].reindex(cal)
    idx_open = uni["idx_open"].reindex(cal)
    idx_close = uni["idx_close"].reindex(cal)
    valid = open_px.notna() & close_px.notna() & close_px.shift(1).notna() & (open_px > 0)
    score = momentum_score(close_px)
    risk_on = index_risk_on(idx_close)
    last = cal.max().strftime("%Y-%m-%d")
    n_stocks = int(uni.get("n_stocks") or close_px.shape[1])
    rows = []
    schemes = {}
    for spec in VARIANTS:
        w = build_weights(
            score,
            valid,
            risk_on,
            long_gross=spec["long_gross"],
            short_gross=spec["short_gross"],
            allow_short=spec["short_gross"] > 0,
            risk_off_scale=spec["risk_off_scale"],
        )
        daily = signed_portfolio(w, open_px, close_px, idx_open, idx_close)
        windows = _windows(daily, last)
        rows.append(_row(spec, windows))
        holdings = write_target(spec, w, risk_on, uni.get("names") or {}, score)
        schemes[spec["id"]] = _scheme_pack(spec, daily, last, n_stocks, windows, holdings)
    payload = {
        "ok": True,
        "updated": date.today().isoformat(),
        "start": REPORT_START,
        "end": last,
        "default_id": "now",
        "note": (
            "同一套 20 日动量、T+1 开盘、佣金/滑点/印花税/融券。只改仓位结构。"
            "不是在报告窗口上搜参。点一行，回测页换成该仓位；（当前）跟着点中的行。"
            "示意图蓝点仍是产品默认 70/30 半仓。空仓三档（70/30、85/15、只做多）是「允许空仓」对照，不是当前产品。"
        ),
        "rows": rows,
        "schemes": schemes,
        "chart": "/frontier.png",
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    disk = {k: v for k, v in payload.items() if k != "schemes"}
    (OUTPUT_DIR / "frontier.json").write_text(
        json.dumps(_jsonable(disk), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    plot_frontier(rows, OUTPUT_DIR / "frontier.png")
    print(f"已写入 {OUTPUT_DIR / 'frontier.json'}")
    for r in rows:
        mark = " *" if r["current"] else ""
        print(
            f"{r['short']:12s}  rel={r['report_excess_relative']:+.1%}  "
            f"dd={r['report_dd']:+.1%}  train_dd={r['train_dd']:+.1%}{mark}"
        )
    return payload


def run_targets() -> dict:
    """只写最近一次目标仓位，不重画净值图。"""
    uni = load_prices()
    cal = pd.DatetimeIndex(uni["cal"])
    open_px = uni["open"].reindex(cal)
    close_px = uni["close"].reindex(cal)
    idx_close = uni["idx_close"].reindex(cal)
    valid = open_px.notna() & close_px.notna() & close_px.shift(1).notna() & (open_px > 0)
    score = momentum_score(close_px)
    risk_on = index_risk_on(idx_close)
    names = uni.get("names") or {}
    out = {}
    for spec in VARIANTS:
        w = build_weights(
            score,
            valid,
            risk_on,
            long_gross=spec["long_gross"],
            short_gross=spec["short_gross"],
            allow_short=spec["short_gross"] > 0,
            risk_off_scale=spec["risk_off_scale"],
        )
        payload = write_target(spec, w, risk_on, names, score)
        out[spec["id"]] = payload
        print(
            f"{spec['short']:12s}  signal={payload['signal_date']}  "
            f"long={payload['n_long']}  short={payload['n_short']}  "
            f"risk_off={payload['risk_off']}"
        )
    return out
