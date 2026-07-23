from __future__ import annotations

import argparse
import logging

from assistant_platform.app import run_assistant
from assistant_platform.config import load_assistant_config


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def _assistant_reload_target() -> None:
    """watchfiles 子进程入口。"""
    _configure_logging()
    run_assistant(load_assistant_config())


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="assistant-platform")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="Start Assistant Platform API + job loop")
    p_serve.add_argument(
        "--reload",
        action="store_true",
        help="开发模式：监视 pulse/assistant_platform，代码变更时自动重启",
    )
    args = parser.parse_args(argv)
    if args.command == "serve":
        if args.reload:
            try:
                from watchfiles import run_process
            except ImportError as exc:
                raise SystemExit(
                    "请安装 web 依赖以启用热重载：pip install -e '.[web]'"
                ) from exc

            from pulse.dev.reload import python_reload_dirs

            watch_dirs = python_reload_dirs()
            logging.getLogger(__name__).info(
                "开发模式：监视 %s，代码变更时自动重启 assistant", ", ".join(watch_dirs)
            )
            run_process(*watch_dirs, target=_assistant_reload_target)
        else:
            run_assistant(load_assistant_config())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
