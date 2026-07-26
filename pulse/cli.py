from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from pulse.config import load_config
from pulse.storage.db import init_db
from pulse.tenant.context import team_repository
from pulse.util.json_codec import dumps_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pulse")


def _serve_reload_target() -> None:
    """watchfiles 子进程入口（须为模块级函数，Windows 下才可 pickle）。"""
    from pulse.app import run_app

    cfg = load_config(os.environ.get("PULSE_CONFIG", "config.yaml"))
    run_app(cfg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulse", description="Cursor Pulse CLI")
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_reprice = sub.add_parser("reprice", help="Re-estimate Included token costs for a period")
    p_reprice.add_argument("--period", required=True)
    p_reprice.add_argument("--account-id", default=None)

    p_reprice_proxy = sub.add_parser(
        "reprice-proxy",
        help="Re-canonical TurnEnded tokens and re-estimate ProxyKeyUsage cost_cents",
    )
    p_reprice_proxy.add_argument("--loan-id", default=None)
    p_reprice_proxy.add_argument("--proxy-key-id", default=None)

    p_init = sub.add_parser(
        "init-db",
        help="Initialize database schema and seed AI tool catalog (vendors/plans)",
    )
    p_init.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip seeding vendors/plans/trial accounts",
    )

    p_rotate_key = sub.add_parser(
        "rotate-credential-key",
        help="Re-encrypt stored credentials after PULSE_CREDENTIAL_ENCRYPTION_KEY rotation",
    )
    p_rotate_key.add_argument("--old", required=True, help="Current encryption key")
    p_rotate_key.add_argument("--new", required=True, help="New encryption key")
    p_rotate_key.add_argument(
        "--dry-run",
        action="store_true",
        help="Decrypt and count only; do not write",
    )

    p_init_v2 = sub.add_parser("init-v2", help="Initialize v2 AI tool center tables and seed catalog")
    p_init_v2.add_argument("--seed", action="store_true", help="Seed vendors, plans, and trial accounts")

    p_sync_dir = sub.add_parser("sync-directory", help="Sync DingTalk directory into members")
    p_sync_dir.add_argument("--dept-id", type=int, default=None, help="Root department id")

    p_channel = sub.add_parser("channel", help="Start channel adapter + sync scheduler")
    p_channel.add_argument("--reload", action="store_true", help="开发模式：代码变更时自动重启")

    p_serve = sub.add_parser("serve", help="(deprecated) 请改用: pulse channel")
    p_serve.add_argument("--reload", action="store_true", help="开发模式：代码变更时自动重启")

    p_asst = sub.add_parser("assistant", help="Run Assistant Platform")
    p_asst.add_argument("assistant_cmd", choices=["serve"])

    p_web = sub.add_parser("web", help="Start admin web dashboard")
    p_web.add_argument("--host", default=None)
    p_web.add_argument("--port", type=int, default=None)
    p_web.add_argument("--reload", action="store_true", help="开发模式：代码变更时自动重载")

    p_dt = sub.add_parser("dingtalk", help="DingTalk utilities")
    dt_sub = p_dt.add_subparsers(dest="dingtalk_cmd", required=True)
    p_resolve = dt_sub.add_parser("resolve-group", help="Convert chatId to openConversationId")
    p_resolve.add_argument("--chat-id", default=None, help="群 chatId，默认读 DINGTALK_CHAT_ID")
    p_oauth_user = dt_sub.add_parser("oauth-user", help="从 OAuth 授权码解析钉钉 userid（code 5 分钟内有效）")
    p_oauth_user.add_argument("--code", required=True, help="登录回调 URL 中的 code 参数")

    p_teams = sub.add_parser("teams-api", help="Fetch Cursor Teams Admin API summary (stub)")
    p_teams.add_argument("--period", required=True)

    p_memory = sub.add_parser("memory", help="Digital employee memory utilities")
    mem_sub = p_memory.add_subparsers(dest="memory_cmd", required=True)
    mem_sub.add_parser("evolve", help="Run memory self-evolution (learned principles)")

    p_admin = sub.add_parser("admin", help="Portal admin utilities")
    admin_sub = p_admin.add_subparsers(dest="admin_cmd", required=True)
    p_bootstrap = admin_sub.add_parser("bootstrap", help="Create first portal owner with password")
    p_bootstrap.add_argument("--user-id", required=True, help="渠道用户 ID（web 本地可用 admin）")
    p_bootstrap.add_argument("--name", default="", help="显示名称")
    p_bootstrap.add_argument("--password", required=True, help="灾备登录密码")
    p_bootstrap.add_argument(
        "--channel",
        default="web",
        choices=["web", "dingtalk", "feishu"],
        help="身份渠道（默认 web）",
    )
    p_grant = admin_sub.add_parser("grant", help="Grant portal role to a member")
    p_grant.add_argument("--user-id", required=True)
    p_grant.add_argument("--name", default="")
    p_grant.add_argument("--role", required=True, choices=["owner", "operator", "auditor", "ai_member", "custom"])
    p_grant.add_argument("--permissions", default="", help="custom 角色时的能力码，逗号分隔")
    p_revoke = admin_sub.add_parser("revoke", help="取消成员的后台访问权限")
    p_revoke.add_argument("--user-id", required=True, help="channel_user_id")
    p_delete = admin_sub.add_parser("delete", help="删除无提交记录的成员")
    p_delete.add_argument("--user-id", required=True, help="channel_user_id")

    p_import_ai = admin_sub.add_parser(
        "import-ai-members",
        help="从钉钉通讯录按姓名导入成员并授予 AI 工具成员角色",
    )
    p_import_ai.add_argument(
        "--names",
        required=True,
        help="姓名列表，逗号分隔，例如：熊波,马静,朱涛",
    )
    p_import_ai.add_argument("--dept-id", type=int, default=None, help="通讯录根部门 id，默认配置值")

    _dev_services = ["web", "admin", "channel", "assistant", "proxy"]
    p_dev = sub.add_parser("dev", help="开发模式服务管理 (start/stop/restart/logs/status)")
    dev_sub = p_dev.add_subparsers(dest="dev_cmd", required=True)
    p_dev_start = dev_sub.add_parser(
        "start",
        help="启动开发服务 (默认 web + assistant + channel + admin + proxy)",
    )
    p_dev_start.add_argument(
        "services",
        nargs="*",
        choices=_dev_services,
        help="web=API, admin=Vue前端, channel=渠道适配, assistant=Assistant Platform, proxy=Cursor代理",
    )
    p_dev_stop = dev_sub.add_parser("stop", help="停止开发服务")
    p_dev_stop.add_argument("services", nargs="*", choices=_dev_services)
    p_dev_restart = dev_sub.add_parser("restart", help="重启开发服务")
    p_dev_restart.add_argument("services", nargs="*", choices=_dev_services)
    p_dev_logs = dev_sub.add_parser("logs", help="查看服务日志")
    p_dev_logs.add_argument("service", choices=_dev_services)
    p_dev_logs.add_argument("-f", "--follow", action="store_true", help="持续跟踪新日志")
    p_dev_logs.add_argument("-n", "--lines", type=int, default=50, help="显示最近 N 行")
    dev_sub.add_parser("status", help="查看服务运行状态")

    args = parser.parse_args(argv)

    if args.command == "dev":
        from pulse.dev.manager import DevManagerError, logs, print_status, restart, start, stop

        try:
            if args.dev_cmd == "start":
                start(args.services or None, config_path=args.config)
            elif args.dev_cmd == "stop":
                stop(args.services or None)
            elif args.dev_cmd == "restart":
                restart(args.services or None, config_path=args.config)
            elif args.dev_cmd == "logs":
                logs(args.service, follow=args.follow, lines=args.lines)
            elif args.dev_cmd == "status":
                print_status()
        except DevManagerError as exc:
            print(exc)
            return 1
        return 0

    config = load_config(args.config)
    session_factory = init_db(config.storage.database_url)

    if args.command == "init-db":
        if not args.no_seed:
            session = session_factory()
            try:
                team, _repo = team_repository(session, config)
                from pulse.tool_center.seed import seed_v2_catalog

                counts = seed_v2_catalog(session, team)
                session.commit()
                logger.info(
                    "Database initialized at %s (seed: vendors=%s plans=%s accounts=%s)",
                    config.storage.database_url,
                    counts["vendors"],
                    counts["plans"],
                    counts["accounts"],
                )
            finally:
                session.close()
        else:
            logger.info("Database initialized at %s (seed skipped)", config.storage.database_url)
        return 0

    if args.command == "rotate-credential-key":
        from pulse.ingestion.credentials import rotate_credential_encryption

        session = session_factory()
        try:
            stats = rotate_credential_encryption(
                session,
                old_key=args.old,
                new_key=args.new,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            logger.error("%s", exc)
            session.close()
            return 1
        session.close()
        mode = "dry-run" if args.dry_run else "rotated"
        logger.info(
            "%s: credentials=%s loan_aliases=%s proxy_keys=%s skipped=%s",
            mode,
            stats["credentials"],
            stats["loan_aliases"],
            stats["proxy_keys"],
            stats["skipped"],
        )
        if stats["skipped"]:
            logger.warning(
                "Some rows could not be decrypted with --old key; "
                "verify backup and old key before updating env"
            )
            return 1
        return 0

    if args.command == "init-v2":
        session = session_factory()
        team, _repo = team_repository(session, config)
        counts = {"vendors": 0, "plans": 0, "accounts": 0}
        if args.seed:
            from pulse.tool_center.seed import seed_v2_catalog

            counts = seed_v2_catalog(session, team)
            session.commit()
        session.close()
        logger.info(
            "V2 schema ready at %s (seed: vendors=%s plans=%s accounts=%s)",
            config.storage.database_url,
            counts["vendors"],
            counts["plans"],
            counts["accounts"],
        )
        return 0

    if args.command == "sync-directory":
        from pulse.integrations.dingtalk_directory import sync_dingtalk_directory

        session = session_factory()
        team, repo = team_repository(session, config)
        stats = sync_dingtalk_directory(repo, config, dept_id=args.dept_id)
        repo.commit()
        session.close()
        print(json.dumps(stats, ensure_ascii=False))
        return 0

    if args.command == "reprice":
        from pulse.pricing.reprice import reprice_period

        session = session_factory()
        team, _repo = team_repository(session, config)
        results = reprice_period(
            session,
            team_id=team.id,
            period=args.period,
            account_id=args.account_id,
        )
        session.commit()
        print(dumps_json(results))
        session.close()
        return 0

    if args.command == "reprice-proxy":
        from pulse.proxy import service as proxy_service

        session = session_factory()
        result = proxy_service.reprice_proxy_usages(
            session,
            loan_id=args.loan_id,
            proxy_key_id=args.proxy_key_id,
        )
        session.commit()
        print(dumps_json(result))
        session.close()
        return 0

    if args.command in ("channel", "serve"):
        from pulse.app import run_app

        if args.command == "serve":
            logger.warning("「pulse serve」已弃用，请改用「pulse channel」")

        if args.reload:
            try:
                from watchfiles import run_process
            except ImportError as exc:
                raise SystemExit("请安装 web 依赖以启用热重载：pip install -e '.[web]'") from exc
            os.environ["PULSE_CONFIG"] = args.config
            from pulse.dev.reload import python_reload_dirs

            watch_dirs = python_reload_dirs()
            logger.info("开发模式：监视 %s，代码变更时自动重启 channel", ", ".join(watch_dirs))
            run_process(*watch_dirs, target=_serve_reload_target)
        else:
            run_app(config)
        return 0

    if args.command == "assistant" and args.assistant_cmd == "serve":
        from assistant_platform.app import run_assistant

        run_assistant()
        return 0

    if args.command == "web":
        try:
            import uvicorn
        except ImportError as exc:
            print("请安装 web 依赖：pip install -e '.[web]'")
            return 1
        host = args.host or config.web.host
        port = args.port or config.web.port
        if args.reload:
            os.environ["PULSE_CONFIG"] = args.config
            from pulse.dev.reload import python_reload_dirs

            uvicorn.run(
                "pulse.web.dev:app",
                host=host,
                port=port,
                reload=True,
                reload_dirs=python_reload_dirs(),
                log_level="info",
            )
        else:
            from pulse.web.app import create_app

            app = create_app(config, session_factory, require_admin_spa=True)
            uvicorn.run(app, host=host, port=port)
        return 0

    if args.command == "teams-api":
        from pulse.integrations.cursor_teams import CursorTeamsClient

        client = CursorTeamsClient(config.cursor_teams)
        try:
            data = client.fetch_usage_summary(args.period)
        except RuntimeError as exc:
            print(exc)
            return 1
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.command == "dingtalk" and args.dingtalk_cmd == "oauth-user":
        from pulse.web.dingtalk_oauth import DingTalkOAuthError, exchange_code_for_userid

        try:
            userid, name = exchange_code_for_userid(config, args.code)
        except DingTalkOAuthError as exc:
            print(exc)
            return 1
        print(f"channel_user_id: {userid}")
        print(f"display_name: {name}")
        print(f"\npulse admin bootstrap --user-id {userid} --name \"{name}\" --password <密码>")
        return 0

    if args.command == "dingtalk" and args.dingtalk_cmd == "resolve-group":
        from pulse.channels.dingtalk.messenger import DingTalkMessenger
        from pulse.channels.dingtalk.work_group import persist_work_group_binding

        chat_id = args.chat_id or config.dingtalk.chat_id
        if not chat_id:
            print("请提供 --chat-id 或在 .env 设置 DINGTALK_CHAT_ID")
            return 1
        messenger = DingTalkMessenger(config)
        try:
            open_id = messenger.resolve_open_conversation_id(chat_id)
        except RuntimeError as exc:
            print(exc)
            return 1
        session = session_factory()
        try:
            team, _repo = team_repository(session, config)
            persist_work_group_binding(
                config,
                session,
                team_id=team.id,
                open_conversation_id=open_id,
                chat_id=chat_id,
                title=config.dingtalk.group_title or None,
                member_id=None,
            )
            session.commit()
        finally:
            session.close()
        print(f"openConversationId: {open_id}")
        print("已写入 team_settings.dingtalk（pulse.db）")
        print("也可设置 .env: DINGTALK_GROUP_ID=" + open_id)
        return 0

    if args.command == "memory" and args.memory_cmd == "evolve":
        print("Memory evolution disabled pending assistant_platform/memory/semantic migration.")
        return 0

    if args.command == "admin" and args.admin_cmd == "bootstrap":
        from pulse.web.portal import bootstrap_portal_owner

        session = session_factory()
        _team, repo = team_repository(session, config)
        member = bootstrap_portal_owner(
            repo,
            channel_user_id=args.user_id,
            display_name=args.name or args.user_id,
            password=args.password,
            channel=args.channel,
        )
        repo.commit()
        print(
            f"Portal owner: {member.display_name} "
            f"({member.channel}:{member.channel_user_id})"
        )
        session.close()
        return 0

    if args.command == "admin" and args.admin_cmd == "grant":
        from pulse.web.portal import grant_portal_role

        session = session_factory()
        team, _repo = team_repository(session, config)
        perms = [p.strip() for p in args.permissions.split(",") if p.strip()] if args.permissions else None
        member = grant_portal_role(
            session,
            team.id,
            args.user_id,
            role=args.role,
            display_name=args.name,
            permissions=perms,
        )
        session.commit()
        print(f"Granted {args.role} to {member.display_name} ({member.channel_user_id})")
        session.close()
        return 0

    if args.command == "admin" and args.admin_cmd == "revoke":
        from pulse.web.portal import PortalAdminError, revoke_portal_access

        session = session_factory()
        team, _repo = team_repository(session, config)
        try:
            member = revoke_portal_access(session, team.id, args.user_id)
        except PortalAdminError as exc:
            print(exc)
            session.close()
            return 1
        session.commit()
        print(f"Revoked portal access: {member.display_name} ({member.channel_user_id})")
        session.close()
        return 0

    if args.command == "admin" and args.admin_cmd == "delete":
        from pulse.web.portal import PortalAdminError, delete_member_without_ingestions

        session = session_factory()
        team, _repo = team_repository(session, config)
        try:
            member = delete_member_without_ingestions(session, team.id, args.user_id)
        except PortalAdminError as exc:
            print(exc)
            session.close()
            return 1
        session.commit()
        print(f"Deleted member: {member.display_name} ({member.channel_user_id})")
        session.close()
        return 0

    if args.command == "admin" and args.admin_cmd == "import-ai-members":
        from pulse.integrations.dingtalk_directory import import_ai_members_by_names

        session = session_factory()
        team, repo = team_repository(session, config)
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        try:
            result = import_ai_members_by_names(
                session,
                team.id,
                repo,
                config,
                names,
                dept_id=args.dept_id,
            )
        except RuntimeError as exc:
            print(exc)
            session.close()
            return 1
        session.commit()
        session.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["missing"] or result["ambiguous"]:
            return 1
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
