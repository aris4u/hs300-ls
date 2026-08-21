"""按 21 个交易日换仓，打开界面后全自动拉数并重跑账本。

和项目一一样：界面开着就在后台转。收盘后才动，盘中不写未收盘 K。
换仓日（或错过换仓）才拉成分/个股并重算；不是每个交易日刷净值。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
from datetime import date, time as dtime, timedelta
from pathlib import Path

import pandas as pd

from hs300_ls.config import OUTPUT_DIR, REBALANCE_BARS, ROOT
from hs300_ls.download import (
    cache_covers,
    fetch_constituents,
    fetch_hs300,
    last_closed_session,
    local_csv_ready,
    now_cn,
    official_end_key,
)

STATE_FILE = OUTPUT_DIR / "update_stamp.json"
LOG_FILE = OUTPUT_DIR / "update.log"
BAR_READY = dtime(15, 20)
TASK_DAILY = "HS300-LS-rebalance"
TASK_LOGON = "HS300-LS-rebalance-logon"

_lock = threading.Lock()
_loop_started = False
_state: dict = {
    "running": False,
    "ok": False,
    "step": "",
    "message": "",
    "error": None,
    "closed": None,
    "asof": None,
    "last_full_asof": None,
    "checked_closed": None,
    "rebalance": False,
    "bars_to_next": None,
    "updated_at": None,
    "generation": 0,
}


def _persist() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(_state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _set(**kwargs) -> None:
    _state.update(kwargs)
    _persist()


def _log(msg: str) -> None:
    line = f"{now_cn().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _load() -> None:
    if not STATE_FILE.exists():
        return
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    for key in (
        "ok",
        "asof",
        "closed",
        "last_full_asof",
        "checked_closed",
        "rebalance",
        "bars_to_next",
        "updated_at",
        "error",
        "message",
        "generation",
    ):
        if key in saved:
            _state[key] = saved[key]


def _mtime(path: Path) -> int | None:
    if not path.exists():
        return None
    return int(path.stat().st_mtime)


def _holdings_asof() -> str | None:
    path = OUTPUT_DIR / "book_holdings.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    asof = raw.get("asof") or raw.get("signal_date")
    return str(asof)[:10] if asof else None


def last_full_asof() -> str | None:
    got = _state.get("last_full_asof")
    if got:
        return str(got)[:10]
    return _holdings_asof()


def _python_for_task() -> str:
    exe = Path(sys.executable)
    hidden = exe.with_name("pythonw.exe")
    return str(hidden if hidden.exists() else exe)


def _task_tr() -> str:
    return f'"{_python_for_task()}" "{ROOT / "run_update.py"}" --once'


def tasks_installed() -> bool:
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_DAILY],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode == 0


def install_tasks() -> dict:
    """额外写一条每日 15:25 任务：关界面也能查。打开界面本身已经会全自动。"""
    tr = _task_tr()
    daily = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            TASK_DAILY,
            "/TR",
            tr,
            "/SC",
            "DAILY",
            "/ST",
            "15:25",
            "/RI",
            "15",
            "/DU",
            "03:00",
            "/F",
            "/RL",
            "LIMITED",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if daily.returncode != 0:
        daily = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                TASK_DAILY,
                "/TR",
                tr,
                "/SC",
                "DAILY",
                "/ST",
                "15:25",
                "/F",
                "/RL",
                "LIMITED",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    ok = daily.returncode == 0
    if ok:
        _log("已写入每日 15:25 自动检查（和项目一一样，开着界面也会自己跑）")
    else:
        _log(f"计划任务写入失败：{(daily.stderr or daily.stdout or '').strip()}")
    return {
        "ok": ok,
        "daily": (daily.stdout or daily.stderr or "").strip(),
        "scheduled": ok,
    }


def uninstall_tasks() -> dict:
    out = []
    ok = True
    for name in (TASK_DAILY, TASK_LOGON):
        r = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out.append((name, (r.stdout or r.stderr or "").strip()))
        if r.returncode != 0 and "cannot find" not in (r.stderr or "").lower() and "找不到" not in (r.stderr or r.stdout or ""):
            ok = False
    _log("已删除换仓计划任务" if ok else "删除计划任务时有错误")
    return {"ok": ok, "detail": out}


def ensure_scheduled() -> None:
    if tasks_installed():
        return
    try:
        install_tasks()
    except Exception as exc:
        _log(f"写入每日检查失败（界面后台循环仍在跑）：{exc}")


def _add_weekdays(start: date, n: int) -> date:
    left = max(0, int(n))
    d = start
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def next_update_date() -> str | None:
    """下次换仓刷新的大致日历日（跳过周末，不含节假日，所以是大约）。"""
    holdings = None
    path = OUTPUT_DIR / "book_holdings.json"
    if path.exists():
        try:
            holdings = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            holdings = None
    if holdings and holdings.get("next_rebalance_date"):
        nxt = str(holdings["next_rebalance_date"])[:10]
        asof_h = str(holdings.get("asof") or "")[:10]
        if nxt and (not asof_h or nxt > asof_h):
            return nxt
    asof = _state.get("asof") or last_full_asof()
    n = _state.get("bars_to_next")
    if holdings:
        asof = asof or holdings.get("asof")
        if n is None:
            n = holdings.get("bars_to_next")
    if not asof or n is None:
        return None
    try:
        start = date.fromisoformat(str(asof)[:10])
    except ValueError:
        return None
    return _add_weekdays(start, int(n)).isoformat()


def _label_cls() -> tuple[str, str]:
    closed = _state.get("closed") or last_closed_session().isoformat()
    asof = _state.get("asof")
    n = _state.get("bars_to_next")
    nxt = next_update_date()
    nxt_txt = f"下次更新大约 {nxt}" if nxt else None
    if _state.get("running"):
        msg = _state.get("message") or "正在拉数并重算仓位"
        if nxt_txt:
            msg = f"{msg}　{nxt_txt}"
        return msg, "warn"
    if _state.get("step") == "wait":
        msg = f"等待收盘K（已有 {asof or '—'}）"
        if nxt_txt:
            msg = f"{msg}　{nxt_txt}"
        return msg, "warn"
    if _state.get("error"):
        return f"自动更新失败：{str(_state['error'])[:40]}", "err"
    if nxt_txt:
        extra = f"　日K截至 {asof}" if asof else ""
        if n is not None:
            extra = f"　约 {n} 个交易日{extra}"
        return nxt_txt + extra, "on"
    if asof:
        extra = ""
        if asof < closed:
            extra = "　待检查换仓"
        return f"日K截至 {asof}{extra}", "on" if not extra else "warn"
    return f"下次更新日期待定（收盘日 {closed}）", "warn"


def status() -> dict:
    with _lock:
        payload = dict(_state)
    now = now_cn()
    closed = last_closed_session().isoformat()
    payload["closed"] = closed
    payload["last_full_asof"] = last_full_asof()
    payload["scheduled"] = tasks_installed()
    payload["book_mtime"] = _mtime(OUTPUT_DIR / "book_metrics.json")
    payload["holdings_mtime"] = _mtime(OUTPUT_DIR / "book_holdings.json")
    payload["next_update_date"] = next_update_date()
    payload["now"] = now.strftime("%Y-%m-%d %H:%M:%S")
    payload["now_date"] = now.strftime("%Y-%m-%d")
    label, cls = _label_cls()
    payload["label"] = label
    payload["cls"] = cls
    payload["ok"] = True
    return payload


def _bars_to_next(n: int, step: int = REBALANCE_BARS) -> int:
    if n <= 0:
        return step
    last_i = n - 1
    rem = last_i % step
    return step if rem == 0 else step - rem


def _last_rebalance_date(hs300: pd.DataFrame, step: int = REBALANCE_BARS) -> str | None:
    if hs300 is None or hs300.empty:
        return None
    dates = pd.to_datetime(hs300["date"]).sort_values().reset_index(drop=True)
    n = len(dates)
    sig_i = ((n - 1) // max(1, int(step))) * max(1, int(step))
    return dates.iloc[int(sig_i)].strftime("%Y-%m-%d")


def _need_full(hs300: pd.DataFrame, *, force: bool) -> bool:
    if force or not local_csv_ready():
        return True
    last_reb = _last_rebalance_date(hs300)
    have = last_full_asof()
    if not last_reb or not have:
        return True
    return have < last_reb


def run_once(*, force: bool = False) -> dict:
    """收盘后检查一次。换仓日、错过换仓、或 force 会拉数并重跑回测。"""
    _load()
    with _lock:
        if _state.get("running"):
            return status()
        _state["running"] = True
        _state["error"] = None
    try:
        return _run_once(force=force)
    finally:
        with _lock:
            _state["running"] = False
            _persist()


def _run_once(*, force: bool) -> dict:
    now = now_cn()
    closed = last_closed_session(now)
    closed_s = closed.isoformat()
    _set(step="check", message=f"收盘日 {closed_s}", closed=closed_s)

    if not force and now.weekday() < 5 and now.time() < BAR_READY:
        _set(step="wait", message="盘中不写未收盘K", ok=True)
        return status()

    _set(step="index", message="检查指数日K")
    hs300 = fetch_hs300(force=False)
    if hs300.empty:
        _set(ok=False, error="指数日K为空")
        _log("自动更新失败：指数日K为空")
        return status()
    asof = str(hs300["date"].max())[:10]
    n = len(hs300)
    bars = _bars_to_next(n)
    last_reb = _last_rebalance_date(hs300)
    need_closed = official_end_key()
    if not force and not cache_covers(hs300["date"].max(), need_closed):
        _set(
            step="wait",
            message="等待数据源今日收盘K",
            asof=asof,
            bars_to_next=bars,
            rebalance=False,
            ok=True,
        )
        return status()

    need_full = _need_full(hs300, force=force)
    if not force and _state.get("checked_closed") == closed_s and not need_full:
        _set(step="idle", message="", asof=asof, bars_to_next=bars, ok=True)
        return status()

    if not need_full:
        _set(
            ok=True,
            asof=asof,
            closed=closed_s,
            checked_closed=closed_s,
            rebalance=False,
            bars_to_next=bars,
            step="idle",
            message="",
            updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            error=None,
        )
        return status()

    _set(step="download", message="换仓日：拉成分和个股日K", rebalance=True, bars_to_next=bars)
    _log(f"自动更新：刷新换仓 {last_reb}（指数截至 {asof}）")
    fetch_constituents(force=True)
    from hs300_ls.prices import refresh_prices

    refresh_prices(force=False)
    _set(step="backtest", message="重跑账本和对照")
    from hs300_ls.book import run_backtest
    from hs300_ls.frontier import run_frontier

    run_backtest()
    run_frontier()
    _set(
        ok=True,
        asof=asof,
        closed=closed_s,
        checked_closed=closed_s,
        last_full_asof=asof,
        rebalance=True,
        bars_to_next=bars if bars else REBALANCE_BARS,
        step="idle",
        message="",
        updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        error=None,
        generation=int(_state.get("generation") or 0) + 1,
    )
    _log(f"自动更新完成 {asof}")
    return status()


def start_background_loop() -> None:
    global _loop_started
    if _loop_started:
        return
    _loop_started = True
    _load()
    ensure_scheduled()

    def loop() -> None:
        while True:
            try:
                run_once(force=False)
            except Exception as exc:
                traceback.print_exc()
                _log(f"自动更新异常：{exc}")
                _set(ok=False, error=str(exc)[:200], running=False)
            time.sleep(60)

    threading.Thread(target=loop, name="ls-rebalance-update", daemon=True).start()
    print("已启动收盘后自动更新（每 21 个交易日换仓才拉数，盘中不写未收盘K）", flush=True)
