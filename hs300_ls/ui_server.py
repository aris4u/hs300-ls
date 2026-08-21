"""项目二浏览器界面。默认 http://127.0.0.1:8766/"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

from hs300_ls.config import OUTPUT_DIR, PORT
from hs300_ls.frontier import VARIANTS

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "ui_static"
VAR_DIR = OUTPUT_DIR / "variants"
_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")


def _safe_id(scheme_id: str) -> str | None:
    sid = (scheme_id or "").strip()
    if not _ID_RE.fullmatch(sid):
        return None
    return sid


def _known_ids() -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        sid = _safe_id(raw)
        if not sid or sid in seen:
            return
        seen.add(sid)
        ids.append(sid)

    for spec in VARIANTS:
        add(spec["id"])
    raw = _frontier_raw()
    for row in (raw or {}).get("rows") or []:
        add(str(row.get("id") or ""))
    if VAR_DIR.exists():
        for path in VAR_DIR.glob("*_metrics.json"):
            add(path.name[: -len("_metrics.json")])
    return ids


def _clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item") and not isinstance(v, (bytes, str, dict, list, tuple)):
        try:
            v = v.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _json_safe(v):
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return _clean(v)


def _read_holdings(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_book() -> dict:
    metrics_path = OUTPUT_DIR / "book_metrics.json"
    if not metrics_path.exists():
        return {"ok": False, "error": "还没有回测。请先运行 python run_backtest.py"}
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    monthly = []
    mpath = OUTPUT_DIR / "book_monthly.csv"
    if mpath.exists():
        df = pd.read_csv(mpath)
        for _, row in df.iterrows():
            monthly.append(
                {
                    "month": str(row["month"]),
                    "strategy": _clean(row["strategy"]),
                    "benchmark": _clean(row["benchmark"]),
                    "excess": _clean(row["excess"]),
                }
            )
    chart = OUTPUT_DIR / "book.png"
    pack = {
        "ok": True,
        "metrics": metrics,
        "monthly": monthly,
        "chart": "/book.png",
        "has_chart": chart.exists(),
    }
    holdings = _read_holdings(OUTPUT_DIR / "book_holdings.json")
    if holdings:
        pack["holdings"] = holdings
    return pack


def load_scheme(scheme_id: str) -> dict:
    sid = _safe_id(scheme_id)
    if not sid:
        return {"ok": False, "error": "未知仓位结构"}
    metrics_path = VAR_DIR / f"{sid}_metrics.json"
    if not metrics_path.exists():
        frontier = _frontier_raw()
        pack = ((frontier or {}).get("schemes") or {}).get(sid)
        if pack:
            return pack
        return {"ok": False, "error": "还没有该仓位的回测。请先运行 python run_backtest.py"}
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    monthly = []
    mpath = VAR_DIR / f"{sid}_monthly.csv"
    if mpath.exists():
        df = pd.read_csv(mpath)
        for _, row in df.iterrows():
            monthly.append(
                {
                    "month": str(row["month"]),
                    "strategy": _clean(row["strategy"]),
                    "benchmark": _clean(row["benchmark"]),
                    "excess": _clean(row["excess"]),
                }
            )
    chart = VAR_DIR / f"{sid}.png"
    pack = {
        "ok": True,
        "id": sid,
        "metrics": metrics,
        "monthly": monthly,
        "chart": f"/variants/{sid}.png",
        "has_chart": chart.exists(),
    }
    holdings = _read_holdings(VAR_DIR / f"{sid}_holdings.json")
    if holdings:
        pack["holdings"] = holdings
    return pack


def _frontier_raw() -> dict | None:
    path = OUTPUT_DIR / "frontier.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_frontier() -> dict:
    payload = _frontier_raw()
    if not payload:
        return {"ok": False, "error": "还没有对照表。请先运行 python run_backtest.py"}
    payload["has_chart"] = (OUTPUT_DIR / "frontier.png").exists()
    payload["chart"] = "/frontier.png"
    payload.setdefault("default_id", "now")
    schemes = payload.get("schemes") or {}
    for sid in _known_ids():
        if sid not in schemes or not schemes[sid].get("ok"):
            pack = load_scheme(sid)
            if pack.get("ok"):
                schemes[sid] = pack
    payload["schemes"] = schemes
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args
        if "/api/tdx" in msg or "/api/update" in msg:
            return
        sys.stderr.write("[ui] " + msg + "\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(_json_safe(payload), ensure_ascii=False, default=str, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        if path == "/" or path == "/index.html":
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/book":
            sid = (qs.get("id") or [""])[0].strip()
            self._json(load_scheme(sid) if sid else load_book())
            return
        if path == "/api/frontier":
            self._json(load_frontier())
            return
        if path == "/api/update":
            from hs300_ls.updater import status

            self._json(status())
            return
        if path == "/api/tdx":
            qs = parse_qs(parsed.query)
            raw = (qs.get("codes") or [""])[0]
            codes = [c.strip() for c in raw.split(",") if c.strip()] or ["000300.SH"]
            try:
                from hs300_ls.tdx import tdx_snap

                self._json(tdx_snap(codes))
            except Exception as exc:
                self._json({"ok": False, "connected": False, "label": "通达信未连接", "error": str(exc), "quotes": {}}, 500)
            return
        if path in {"/book.png", "/frontier.png"}:
            self._png(OUTPUT_DIR / path.lstrip("/"))
            return
        if path.startswith("/variants/") and path.endswith(".png"):
            sid = _safe_id(path[len("/variants/") : -4])
            if not sid:
                self._send(404, b"not found", "text/plain")
                return
            self._png(VAR_DIR / f"{sid}.png")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if path == "/api/update":
            from hs300_ls.updater import run_once, status

            threading.Thread(target=run_once, kwargs={"force": True}, daemon=True).start()
            self._json(status())
            return
        self._send(404, b"not found", "text/plain")

    def _png(self, file: Path) -> None:
        if not file.exists():
            self._send(404, b"missing", "text/plain")
            return
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])


def open_app_window(url: str) -> None:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(local) / "Microsoft/Edge/Application/msedge.exe",
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path(local) / "Google/Chrome/Application/chrome.exe",
    ]
    for exe in candidates:
        if exe.exists():
            subprocess.Popen(
                [str(exe), f"--app={url}", "--window-size=1560,960", "--window-position=80,40"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="理想约束选股界面")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    port = _free_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"项目二界面  {url}")
    print("关掉这个窗口即退出。理想约束回测，不是投资建议。")
    from hs300_ls.updater import start_background_loop

    start_background_loop()
    if not args.no_browser:
        threading.Timer(0.4, open_app_window, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
    return 0
