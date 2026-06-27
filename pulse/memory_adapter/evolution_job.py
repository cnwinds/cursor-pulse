from __future__ import annotations

import logging

from pulse.config import AppConfig
from pulse.memory_adapter.identity import team_id_to_namespace
from pulse.memory_adapter.executor import PulseActionExecutor
from pulse.memory_adapter.wiring import build_memory_engine
from pulse.tenant.context import team_repository

logger = logging.getLogger(__name__)


def run_memory_evolution(
    session_factory,
    config: AppConfig,
    *,
    send_private_message=None,
    send_group_message=None,
) -> dict:
    if not config.memory.evolution_enabled:
        return {"principles": 0, "actions": 0}

    session = session_factory()
    try:
        team, repo = team_repository(session, config)
        engine = build_memory_engine(
            session,
            config,
            team.id,
            pulse_repo=repo,
            send_private_message=send_private_message,
            send_group_message=send_group_message,
        )
        namespace = team_id_to_namespace(team.id)
        result = engine.evolve(namespace, min_confidence=config.memory.evolution_min_confidence)
        session.commit()
        if result.principles or result.actions:
            logger.info(
                "Memory evolution: +%d principles, %d actions for %s",
                len(result.principles),
                len(result.actions),
                namespace,
            )
        return {
            "principles": len(result.principles),
            "actions": len([a for a in result.actions if a.status == "executed"]),
        }
    except Exception:
        logger.exception("Memory evolution failed")
        session.rollback()
        return {"principles": 0, "actions": 0}
    finally:
        session.close()
