"""价格动量：做多最强、做空最弱。弱市半仓，不空仓。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from hs300_ls.config import (
    ALLOW_SHORT,
    INDEX_MA,
    LONG_GROSS,
    MIN_LONG,
    MOM_LOOKBACK,
    MOM_SKIP,
    REBALANCE_BARS,
    RISK_OFF_SCALE,
    SHORT_GROSS,
    SHORT_N,
    TOP_N,
)


def momentum_score(close: pd.DataFrame, *, lookback: int = MOM_LOOKBACK, skip: int = MOM_SKIP) -> pd.DataFrame:
    return close.shift(skip) / close.shift(skip + lookback) - 1


def index_risk_on(idx_close: pd.Series, *, ma: int = INDEX_MA) -> pd.Series:
    if ma <= 0:
        return pd.Series(True, index=idx_close.index)
    avg = idx_close.astype(float).rolling(ma, min_periods=ma).mean()
    return (idx_close.astype(float) > avg).fillna(False)


def _rebalance_hold(w: pd.DataFrame, step: int = REBALANCE_BARS) -> pd.DataFrame:
    step = max(1, int(step))
    arr = w.to_numpy(dtype=float, copy=False)
    idx = (np.arange(len(arr)) // step) * step
    return pd.DataFrame(arr[idx], index=w.index, columns=w.columns)


def build_weights(
    score: pd.DataFrame,
    valid: pd.DataFrame,
    risk_on: pd.Series,
    *,
    top_n: int = TOP_N,
    short_n: int = SHORT_N,
    allow_short: bool = ALLOW_SHORT,
    min_long: int = MIN_LONG,
    rebalance_bars: int = REBALANCE_BARS,
    long_gross: float = LONG_GROSS,
    short_gross: float = SHORT_GROSS,
    risk_off_scale: float = RISK_OFF_SCALE,
) -> pd.DataFrame:
    floor = min(int(min_long), int(top_n))
    sc = score.where(valid)
    on = risk_on.reindex(score.index).fillna(False).astype(bool)
    long_rank = sc.rank(axis=1, ascending=False, method="first")
    long = valid & (long_rank <= float(top_n))
    n_long = long.sum(axis=1)
    w_long = long.astype(float).div(n_long.replace(0, np.nan), axis=0).fillna(0.0) * float(long_gross)
    w = w_long
    if allow_short and short_n > 0 and short_gross > 0:
        short_rank = sc.rank(axis=1, ascending=True, method="first")
        short = valid & (short_rank <= float(short_n)) & ~long
        ns = short.sum(axis=1)
        w = w.sub(
            short.astype(float).div(ns.replace(0, np.nan), axis=0).fillna(0.0) * float(short_gross),
            fill_value=0.0,
        )
    scale = pd.Series(np.where(on.to_numpy(), 1.0, float(risk_off_scale)), index=score.index)
    w = w.mul(scale, axis=0)
    too_thin = n_long < floor
    w.loc[too_thin] = 0.0
    return _rebalance_hold(w.fillna(0.0), rebalance_bars)
