"""按 21 个交易日换仓，全自动拉数并重跑账本。

用法：
    python run_update.py --install   # 写入 Windows 计划任务（关界面也会跑）
    python run_update.py --uninstall
    python run_update.py --once      # 检查一次（计划任务调用这个）
    python run_update.py --force     # 立刻拉数并回测
    python run_update.py             # 常驻循环（一般不用，计划任务即可）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="项目二换仓全自动更新")
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--force", action="store_true", help="立刻拉数并回测")
    parser.add_argument("--install", action="store_true", help="安装 Windows 计划任务")
    parser.add_argument("--uninstall", action="store_true", help="删除计划任务")
    args = parser.parse_args()
    from hs300_ls.updater import install_tasks, run_once, start_background_loop, status, uninstall_tasks

    if args.install:
        result = install_tasks()
        print(status().get("label") or "")
        return 0 if result.get("ok") else 1
    if args.uninstall:
        result = uninstall_tasks()
        print(result)
        return 0 if result.get("ok") else 1
    if args.once or args.force:
        run_once(force=args.force)
        print(status().get("label") or "")
        return 0
    start_background_loop()
    print("常驻中。Ctrl+C 退出。和项目一一样，开着 HS300-LS.cmd 就会自己跑。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
