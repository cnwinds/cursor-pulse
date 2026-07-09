from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from pulse.aggregate.engine import aggregate_period
from pulse.config import load_config
from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.extract.summary import format_private_confirmation
from pulse.storage.db import init_db
from pulse.tenant.context import team_repository
from pulse.util.json_codec import dumps_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pulse")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulse", description="Cursor Pulse CLI")
    parser.add_argument("-c", "--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Parse usage-events CSV and print summary")
    p_parse.add_argument("csv_path", type=Path)

    p_import = sub.add_parser("import", help="Import CSV into database")
    p_import.add_argument("csv_path", type=Path)
    p_import.add_argument("--user-id", required=True)
    p_import.add_argument("--name", default="")
    p_import.add_argument("--period", required=True)

    p_agg = sub.add_parser("aggregate", help="Run aggregation for a billing period")
    p_agg.add_argument("--period", required=True)

    p_report = sub.add_parser("report", help="Generate and print monthly report")
    p_report.add_argument("--period", required=True)
    p_report.add_argument("--publish", action="store_true", help="Post to DingTalk group")
    p_report.add_argument("--pdf", type=Path, default=None, help="Export PDF to path")

    p_export = sub.add_parser("export", help="Export usage records to CSV")
    p_export.add_argument("--period", required=True)
    p_export.add_argument("-o", "--output", type=Path, default=None)

    p_reprice = sub.add_parser("reprice", help="Re-estimate Included token costs for a period")
    p_reprice.add_argument("--period", required=True)
    p_reprice.add_argument("--account-id", default=None)

    p_init = sub.add_parser("init-db", help="Initialize database schema")

    p_init_v2 = sub.add_parser("init-v2", help="Initialize v2 AI tool center tables and seed catalog")
    p_init_v2.add_argument("--seed", action="store_true", help="Seed vendors, plans, and trial accounts")

    p_sync_dir = sub.add_parser("sync-directory", help="Sync DingTalk directory into members")
    p_sync_dir.add_argument("--dept-id", type=int, default=None, help="Root department id")

    p_serve = sub.add_parser("serve", help="Start DingTalk bot + reminder scheduler")

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

    p_remind = sub.add_parser("remind", help="Trigger reminder manually")
    p_remind.add_argument("kind", choices=["start", "daily", "deadline", "report"])
    p_remind.add_argument("--period")

    p_alerts = sub.add_parser("alerts", help="Run anomaly detection")
    p_alerts.add_argument("--period", required=True)

    p_bi = sub.add_parser("bi-push", help="Push metrics snapshot to BI webhook")
    p_bi.add_argument("--period", required=True)

    p_teams = sub.add_parser("teams-api", help="Fetch Cursor Teams Admin API summary (stub)")
    p_teams.add_argument("--period", required=True)

    p_memory = sub.add_parser("memory", help="Digital employee memory utilities")
    mem_sub = p_memory.add_subparsers(dest="memory_cmd", required=True)
    mem_sub.add_parser("evolve", help="Run memory self-evolution (learned principles)")

    p_admin = sub.add_parser("admin", help="Portal admin utilities")
    admin_sub = p_admin.add_subparsers(dest="admin_cmd", required=True)
    p_bootstrap = admin_sub.add_parser("bootstrap", help="Create first portal owner with password")
    p_bootstrap.add_argument("--user-id", required=True, help="钉钉 dingtalk_user_id")
    p_bootstrap.add_argument("--name", default="", help="显示名称")
    p_bootstrap.add_argument("--password", required=True, help="灾备登录密码")
    p_grant = admin_sub.add_parser("grant", help="Grant portal role to a member")
    p_grant.add_argument("--user-id", required=True)
    p_grant.add_argument("--name", default="")
    p_grant.add_argument("--role", required=True, choices=["owner", "operator", "auditor", "ai_member", "custom"])
    p_grant.add_argument("--permissions", default="", help="custom 角色时的能力码，逗号分隔")
    p_revoke = admin_sub.add_parser("revoke", help="取消成员的后台访问权限")
    p_revoke.add_argument("--user-id", required=True, help="钉钉 dingtalk_user_id")
    p_delete = admin_sub.add_parser("delete", help="删除无提交记录的成员")
    p_delete.add_argument("--user-id", required=True, help="钉钉 dingtalk_user_id")

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

    p_dev = sub.add_parser("dev", help="开发模式服务管理 (start/stop/restart/logs/status)")
    dev_sub = p_dev.add_subparsers(dest="dev_cmd", required=True)
    p_dev_start = dev_sub.add_parser("start", help="启动开发服务 (默认 web + admin + bot)")
    p_dev_start.add_argument(
        "services",
        nargs="*",
        choices=["web", "admin", "bot"],
        help="web=API, admin=Vue前端, bot=钉钉机器人",
    )
    p_dev_stop = dev_sub.add_parser("stop", help="停止开发服务")
    p_dev_stop.add_argument("services", nargs="*", choices=["web", "admin", "bot"])
    p_dev_restart = dev_sub.add_parser("restart", help="重启开发服务")
    p_dev_restart.add_argument("services", nargs="*", choices=["web", "admin", "bot"])
    p_dev_logs = dev_sub.add_parser("logs", help="查看服务日志")
    p_dev_logs.add_argument("service", choices=["web", "admin", "bot"])
    p_dev_logs.add_argument("-f", "--follow", action="store_true", help="持续跟踪新日志")
    p_dev_logs.add_argument("-n", "--lines", type=int, default=50, help="显示最近 N 行")
    dev_sub.add_parser("status", help="查看服务运行状态")

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "parse":
        parsed = parse_usage_events_csv(args.csv_path)
        print(json.dumps(parsed.summary.__dict__, default=str, indent=2, ensure_ascii=False))
        return 0

    session_factory = init_db(config.storage.database_url)

    if args.command == "init-db":
        logger.info("Database initialized at %s", config.storage.database_url)
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

    if args.command == "import":
        session = session_factory()
        _team, repo = team_repository(session, config)
        member = repo.add_member(args.user_id, args.name or args.user_id)
        parsed = parse_usage_events_csv(args.csv_path)
        repo.save_csv_ingestion(
            member=member,
            period=args.period,
            parsed=parsed,
            submit_channel="private",
            raw_source=args.csv_path,
            raw_files_dir=Path(config.storage.raw_files_dir),
        )
        repo.commit()
        print(format_private_confirmation(member.display_name, args.period, parsed.summary))
        session.close()
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

    if args.command == "aggregate":
        session = session_factory()
        team, _repo = team_repository(session, config)
        metrics = aggregate_period(session, args.period, team_id=team.id)
        session.commit()
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
        session.close()
        return 0

    if args.command == "report":
        from pulse.bot.base import create_messenger
        from pulse.report.pdf import write_report_pdf
        from pulse.report.service import generate_report, publish_report_to_group

        session = session_factory()
        team, _repo = team_repository(session, config)
        if args.publish:
            messenger = create_messenger(config)
            text = publish_report_to_group(
                session, args.period, messenger, team_id=team.id, config=config
            )
        else:
            text, _, _ = generate_report(
                session, args.period, team_id=team.id, config=config
            )
        session.commit()
        print(text)
        if args.pdf:
            pdf_path = write_report_pdf(text, args.pdf)
            print(f"PDF: {pdf_path}")
        session.close()
        return 0

    if args.command == "export":
        from pulse.export.exporter import export_usage_csv

        session = session_factory()
        out = args.output or Path(config.storage.raw_files_dir) / f"export_{args.period}.csv"
        path = export_usage_csv(session, args.period, out)
        print(path)
        session.close()
        return 0

    if args.command == "remind":
        from pulse.bot.reminders.scheduler import ReminderService

        def _log_group(text, at_all=False):
            logger.info("[GROUP at_all=%s] %s", at_all, text)

        def _log_private(user_id, text):
            logger.info("[PRIVATE %s] %s", user_id, text)

        service = ReminderService(config, session_factory, _log_group, _log_private)
        period = args.period
        if args.kind in ("start", "daily", "deadline") and not config.collection.reminders_enabled:
            logger.warning(
                "用量提交催办已关闭（collection.reminders_enabled=false）；"
                "跳过 %s。如需手动测试请设置 USAGE_REMINDERS_ENABLED=true",
                args.kind,
            )
            return 0
        if args.kind == "start":
            service.send_collection_start(period)
        elif args.kind == "daily":
            n = service.send_daily_nudges(period)
            logger.info("Sent %d daily nudges", n)
        elif args.kind == "report":
            from pulse.bot.dingtalk.messenger import DingTalkMessenger

            service.messenger = DingTalkMessenger(config)
            service.send_monthly_report(period)
        else:
            service.send_deadline_reminder(period)
        return 0

    if args.command == "serve":
        from pulse.app import run_app

        run_app(config)
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
            project_root = Path(__file__).resolve().parent.parent
            uvicorn.run(
                "pulse.web.dev:app",
                host=host,
                port=port,
                reload=True,
                reload_dirs=[str(project_root / "pulse")],
            )
        else:
            from pulse.web.app import create_app

            app = create_app(config, session_factory)
            uvicorn.run(app, host=host, port=port)
        return 0

    if args.command == "alerts":
        from pulse.alerts.service import run_anomaly_check

        session = session_factory()
        team, _repo = team_repository(session, config)
        rows = run_anomaly_check(session, config, team.id, args.period)
        session.commit()
        print(json.dumps([{"type": r.alert_type, "message": r.message} for r in rows], ensure_ascii=False, indent=2))
        session.close()
        return 0

    if args.command == "bi-push":
        from pulse.integrations.webhook import build_bi_payload, push_webhook
        from pulse.report.service import get_latest_snapshot

        session = session_factory()
        team, _repo = team_repository(session, config)
        snap = get_latest_snapshot(session, args.period, team_id=team.id)
        if not snap:
            print(f"账期 {args.period} 无 snapshot")
            return 1
        payload = build_bi_payload(
            team_slug=team.slug,
            team_name=team.name,
            period=args.period,
            metrics=snap.metrics_json,
        )
        push_webhook(config.integrations.webhook_url, payload, secret=config.integrations.webhook_secret)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        session.close()
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
        print(f"dingtalk_user_id: {userid}")
        print(f"display_name: {name}")
        print(f"\npulse admin bootstrap --user-id {userid} --name \"{name}\" --password <密码>")
        return 0

    if args.command == "dingtalk" and args.dingtalk_cmd == "resolve-group":
        from pulse.bot.dingtalk.group_store import save_group_binding
        from pulse.bot.dingtalk.messenger import DingTalkMessenger

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
        save_group_binding(open_conversation_id=open_id, chat_id=chat_id, title="熊波,马静")
        print(f"openConversationId: {open_id}")
        print("已写入 data/dingtalk_group.json")
        print("也可设置 .env: DINGTALK_GROUP_ID=" + open_id)
        return 0

    if args.command == "memory" and args.memory_cmd == "evolve":
        from pulse.memory_adapter.evolution_job import run_memory_evolution

        count = run_memory_evolution(session_factory, config)
        print(f"Evolution: +{count['principles']} principles, {count['actions']} actions executed")
        return 0

    if args.command == "admin" and args.admin_cmd == "bootstrap":
        from pulse.web.portal import bootstrap_portal_owner

        session = session_factory()
        _team, repo = team_repository(session, config)
        member = bootstrap_portal_owner(
            repo,
            dingtalk_user_id=args.user_id,
            display_name=args.name or args.user_id,
            password=args.password,
        )
        repo.commit()
        print(f"Portal owner: {member.display_name} ({member.dingtalk_user_id})")
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
        print(f"Granted {args.role} to {member.display_name} ({member.dingtalk_user_id})")
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
        print(f"Revoked portal access: {member.display_name} ({member.dingtalk_user_id})")
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
        print(f"Deleted member: {member.display_name} ({member.dingtalk_user_id})")
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
