from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pulse.aggregate.engine import aggregate_period
from pulse.config import load_config
from pulse.extract.csv_parser import parse_usage_events_csv
from pulse.extract.summary import format_private_confirmation
from pulse.storage.db import init_db
from pulse.tenant.context import team_repository

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

    p_init = sub.add_parser("init-db", help="Initialize database schema")

    p_serve = sub.add_parser("serve", help="Start DingTalk bot + reminder scheduler")

    p_web = sub.add_parser("web", help="Start admin web dashboard")
    p_web.add_argument("--host", default=None)
    p_web.add_argument("--port", type=int, default=None)

    p_dt = sub.add_parser("dingtalk", help="DingTalk utilities")
    dt_sub = p_dt.add_subparsers(dest="dingtalk_cmd", required=True)
    p_resolve = dt_sub.add_parser("resolve-group", help="Convert chatId to openConversationId")
    p_resolve.add_argument("--chat-id", default=None, help="群 chatId，默认读 DINGTALK_CHAT_ID")

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
    p_grant.add_argument("--role", required=True, choices=["owner", "operator", "auditor", "custom"])
    p_grant.add_argument("--permissions", default="", help="custom 角色时的能力码，逗号分隔")

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

    if args.command == "import":
        session = session_factory()
        _team, repo = team_repository(session, config)
        member = repo.add_member(args.user_id, args.name or args.user_id)
        parsed = parse_usage_events_csv(args.csv_path)
        repo.save_csv_submission(
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
        from pulse.web.app import create_app

        host = args.host or config.web.host
        port = args.port or config.web.port
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
