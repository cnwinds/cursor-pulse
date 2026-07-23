# Assistant Platform Phases 2–5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Work **on `master`** (user directed). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Complete remaining Assistant Platform stages: session ledger + takeover (2), memory/profiles (3), review + Prompt Studio (4), controlled evolution (5).

> **Status:** Phase 2–5 implemented on `master` (2026-07-14).

**Architecture:** Assistant owns sessions/messages/orchestration/reply outbox. Pulse Channel Adapter mirrors events and, when `ASSISTANT_TAKEOVER=true`, skips legacy reply. Orchestrator consumes jobs, may call ChatService-compatible responder or CapabilityExecutor, writes ledger, sends reply via channel adapter callback. Later phases hang review/memory/prompt on session close.

**Tech Stack:** Python 3.11+, SQLAlchemy, FastAPI, Vue 3, pytest, existing personamem

**Spec:** [2026-07-14-assistant-platform-design.md](../specs/2026-07-14-assistant-platform-design.md) §8–§14, §20 阶段 2–5

**Constraint:** Each phase independently runnable with feature flags; no big-bang cutover.

---

# PHASE 2 — 完整会话账本

**Out of scope for P2:** personamem migration, auto-review, Prompt Studio, evolution

## P2 File map

| Path | Role |
|------|------|
| `assistant_platform/conversation/models.py` | `ap_chat_sessions`, `ap_chat_messages` ORM (or add to storage/models) |
| `assistant_platform/conversation/session_store.py` | open/continue/close |
| `assistant_platform/conversation/orchestrator.py` | process incoming → ledger → reply |
| `assistant_platform/conversation/responder.py` | thin adapter calling Pulse chat OR local rule reply |
| `assistant_platform/api/sessions.py` | GET list/detail, export, delete, close |
| `pulse/config.py` | `AssistantTakeoverConfig` |
| `pulse/channels/dingtalk/handler.py` | takeover branch |
| `pulse/channels/dingtalk/reply_adapter.py` | HTTP callback from Assistant to send DingTalk reply |
| `pulse/web/app.py` | web chat mirror + takeover |
| `pulse/web/assistant_sessions_api.py` | portal proxy |
| `web-admin/src/views/SessionsView.vue` | admin sessions |

### P2-T1: Session + message schema

- [ ] Create `ap_chat_sessions`: id, assistant_id, team_id, channel, conversation_type, conversation_id, user_id (nullable for group), status open|closed, prompt_release_id nullable, opened_at, last_activity_at, closed_at, close_reason
- [ ] Create `ap_chat_messages`: id, session_id, role user|assistant|system|tool|error, text_redacted, secret_refs_json, incoming_event_id nullable, meta_json, created_at
- [ ] Unique open session per session key (partial unique or app-enforced)
- [ ] Tests + commit `feat(assistant): add chat session and message schema`

### P2-T2: SessionStore open/continue/close

```python
def attach_message(session, event: IncomingMessageEvent) -> tuple[ChatSessionRow, ChatMessageRow]:
    # find open session by key; if idle timeout exceeded, close then create new
    # private idle 30m, group 10m
```

- [ ] Tests for continue vs new after timeout
- [ ] Commit `feat(assistant): session open continue and idle close`

### P2-T3: Wire ingest → session attach

- [ ] After successful EventIngestService create, call SessionStore.attach_message; enqueue job `session.process` instead of noop
- [ ] Duplicate ingest does not attach twice
- [ ] Commit `feat(assistant): attach sessions on ingest`

### P2-T4: Orchestrator + reply outbox

- [ ] Job `session.process`: load message, if looks like capability command try CapabilityExecutor else Responder (HTTP to Pulse internal chat OR simplified echo+capability hint)
- [ ] Write assistant message to ledger; outbox `reply.send` with reply_endpoint + text
- [ ] Job `reply.send`: POST Pulse `/api/internal/v1/channel/reply` 
- [ ] Commit `feat(assistant): orchestrate session replies via outbox`

### P2-T5: Pulse channel reply + takeover flag

- [ ] `ASSISTANT_TAKEOVER` + `ASSISTANT_SHADOW_MODE` config
- [ ] `POST /api/internal/v1/channel/reply` — send via DingTalk messenger using reply_endpoint
- [ ] Handler: if takeover, mirror then return (no legacy reply); if shadow, run legacy but also mirror and compare log; if off, legacy only
- [ ] Web `/api/chat`: mirror + optionally proxy to Assistant process endpoint
- [ ] Commit `feat(bot): assistant takeover and channel reply endpoint`

### P2-T6: Sessions admin API + UI

- [ ] Permissions `assistant:sessions:read:self|all`, `export:self|all`, delete
- [ ] Assistant GET `/api/assistant/v1/sessions`, `/{id}`, export, delete (self scoped)
- [ ] Portal proxy + SessionsView (list/filter/detail timeline)
- [ ] Retention job stub: delete messages older than 180 days
- [ ] Commit `feat(admin): session ledger browse export and retention stub`
- [ ] README Phase 2 docs

**P2 Done when:** takeover flag can drive DingTalk text → session → reply; admin can browse timeline; legacy flag off restores old path; tests green.

---

# PHASE 3 — 记忆与画像

### P3-T1: Assistant memory adapter

- [ ] `assistant_platform/memory/wiring.py` — build MemoryEngine using personamem + assistant DB URL or shared pulse DB URL from config
- [ ] Orchestrator uses memory on conversational path; distill only on session close
- [ ] Commit `feat(assistant): wire personamem into conversation orchestrator`

### P3-T2: Profile signals

- [ ] Tables `ap_profile_signals`, `ap_profile_corrections` (source_session_ids, confidence, expires_at)
- [ ] On session close job: distill + extract signals with evidence
- [ ] API: GET/POST correct self profile
- [ ] Tests: private memory not disclosed in group VisibilityContext
- [ ] Commit `feat(assistant): evidence-based profile signals and corrections`

### P3-T3: Migration note + dual-read

- [ ] Document migration: existing pm_* remain source of truth until cutover; adapter reads pm_* 
- [ ] Optional script `assistant_platform/memory/migrate_stub.py` that copies turn counts (no full rewrite required if shared DB)
- [ ] Commit `docs(assistant): phase3 memory boundary and dual-read`

**P3 Done when:** session close triggers distill; group/private disclosure tests pass; user can list/correct own signals.

---

# PHASE 4 — 评审与 Prompt Studio

### P4-T1: Auto review on session close

- [ ] Tables `ap_session_reviews`, `ap_human_reviews`, `ap_review_rubrics`
- [ ] Job `session.review`: heuristic scorer (length, error flags, tool success) → score 0–100 + failure_tags; status completed|failed
- [ ] Low score (<60) or destructive tools → queue human_review
- [ ] Commit `feat(assistant): auto-score ended sessions`

### P4-T2: Prompt fragments + releases

- [ ] Tables `ap_prompt_fragments`, `ap_prompt_releases`, `ap_prompt_deployments`
- [ ] Seed default release `v0-default` with heart/戒律 stubs from config
- [ ] Session open pins `prompt_release_id`
- [ ] Commit `feat(assistant): prompt release pin on session open`

### P4-T3: Prompt Studio API + UI

- [ ] CRUD fragments (owner only); create release from fragments; list releases
- [ ] web-admin PromptStudioView — list releases, view diff text, approve draft→canary
- [ ] Commit `feat(admin): prompt studio minimal release UI`

### P4-T4: Evaluation stub

- [ ] Tables `ap_evaluation_datasets`, `ap_evaluation_cases`, `ap_evaluation_runs`
- [ ] CLI/API: run offline replay of N closed sessions against a release (compare scores)
- [ ] Commit `feat(assistant): evaluation replay stub`

**P4 Done when:** every closed session has review row; releases exist; studio can create draft release.

---

# PHASE 5 — 受控进化

### P5-T1: Failure clusters + proposals

- [ ] Table `ap_failure_clusters`, `ap_prompt_change_proposals` (diff, source_cluster, status)
- [ ] Job: cluster low-score reviews by failure_tags; create proposal draft (human must approve — never auto-apply)
- [ ] Commit `feat(assistant): failure clusters and prompt change proposals`

### P5-T2: Canary deploy + rollback

- [ ] Deployment: percent canary on new sessions; promote; rollback switches production pointer in <1 min for new sessions
- [ ] API + UI buttons: approve proposal → canary 10% → promote / rollback
- [ ] Commit `feat(assistant): prompt canary deploy and rollback`

### P5-T3: Cutover cleanup flags

- [ ] When `ASSISTANT_TAKEOVER=true` and `ASSISTANT_LEGACY_CHAT=false`: handler skips ChatService entirely for text
- [ ] Document deprecation of dual path
- [ ] Final README + spec status Phase 2–5 landed
- [ ] Commit `docs: complete assistant platform phases 2-5`

**P5 Done when:** proposal cannot reach production without approve+canary; rollback works; full test suite for new modules green.

---

## Global acceptance

- [x] Feature flags allow full rollback to Phase 1 behavior
- [x] No plaintext Cursor keys in session messages
- [x] pytest for all new modules green
- [x] Spec status updated
