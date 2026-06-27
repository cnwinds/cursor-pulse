from __future__ import annotations

from personamem import MemoryEngine
from personamem.repository import SqlAlchemyMemoryRepository
from personamem.responders import LlmResponder, RuleBasedResponder
from personamem.reflectors import LlmReflector, RuleBasedReflector
from pulse.config import AppConfig
from pulse.llm.client import build_llm_client
from pulse.memory_adapter.embedder import build_embedder
from pulse.memory_adapter.executor import PulseActionExecutor
from pulse.memory_adapter.identity import team_id_to_namespace
from pulse.memory_adapter.llm import LlmDistiller, LlmReviewer, RuleBasedDistiller, RuleBasedReviewer
from pulse.memory_adapter.persona import persona_from_config
from pulse.storage.repository import Repository


def build_memory_engine(
    session,
    config: AppConfig,
    team_id: str,
    *,
    pulse_repo: Repository | None = None,
    send_private_message=None,
    send_group_message=None,
) -> MemoryEngine:
    repo = SqlAlchemyMemoryRepository(session)
    namespace = team_id_to_namespace(team_id)
    client = build_llm_client(config)

    if client and config.llm.api_key:
        distiller = LlmDistiller(client)
        reviewer = LlmReviewer(client)
        responder = LlmResponder(client)
        reflector = LlmReflector(client)
    else:
        distiller = RuleBasedDistiller()
        reviewer = RuleBasedReviewer()
        responder = RuleBasedResponder()
        reflector = RuleBasedReflector()

    executor = None
    if pulse_repo is not None:
        executor = PulseActionExecutor(
            config=config,
            pulse_repo=pulse_repo,
            team_id=team_id,
            send_private_message=send_private_message,
            send_group_message=send_group_message,
        )

    engine = MemoryEngine(
        repo=repo,
        distiller=distiller,
        reviewer=reviewer,
        responder=responder,
        reflector=reflector,
        executor=executor,
        persona=persona_from_config(config),
        embedder=build_embedder(config, repo=repo, namespace=namespace),
        retrieval_top_k=config.memory.retrieval_top_k,
        conversation_turn_limit=config.memory.conversation_turn_limit,
        conversation_keep=config.memory.conversation_keep,
        evolution_auto_execute=config.memory.evolution_auto_execute,
    )
    engine.ensure_seeded(namespace)
    return engine
