"""Lightweight entry for ``python -m pulse.dev`` (skips heavy ``pulse.cli`` imports)."""

from __future__ import annotations

import argparse
import sys

_DEV_SERVICES = ["web", "admin", "channel", "assistant", "proxy"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulse.dev", description="Cursor Pulse dev service manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="启动开发服务")
    p_start.add_argument("services", nargs="*", choices=_DEV_SERVICES)

    p_stop = sub.add_parser("stop", help="停止开发服务")
    p_stop.add_argument("services", nargs="*", choices=_DEV_SERVICES)

    p_restart = sub.add_parser("restart", help="重启开发服务")
    p_restart.add_argument("services", nargs="*", choices=_DEV_SERVICES)

    p_logs = sub.add_parser("logs", help="查看服务日志")
    p_logs.add_argument("service", nargs="?", default="web", choices=_DEV_SERVICES)
    p_logs.add_argument("-f", "--follow", action="store_true", help="持续跟踪新日志")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="显示最近 N 行")

    sub.add_parser("status", help="查看服务运行状态")

    args = parser.parse_args(argv)

    from pulse.dev.manager import DevManagerError, logs, print_status, restart, start, stop

    try:
        if args.cmd == "start":
            start(args.services or None)
        elif args.cmd == "stop":
            stop(args.services or None)
        elif args.cmd == "restart":
            restart(args.services or None)
        elif args.cmd == "logs":
            logs(args.service, follow=args.follow, lines=args.lines)
        elif args.cmd == "status":
            print_status()
    except DevManagerError as exc:
        print(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
