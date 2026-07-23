from sqlalchemy import func, select, text

from assistant_platform.capabilities.catalog import (
    CAPABILITY_OPERATIONS,
    OWNER_EXTRA_KEYS,
    SELF_SERVICE_KEYS,
)
from assistant_platform.capabilities.models import (
    CapabilityAssignmentRow,
    CapabilityDefinitionRow,
    CapabilityPackItemRow,
    CapabilityPackRow,
    CapabilityVersionRow,
)
from assistant_platform.capabilities.seed import seed_phase1_capabilities
from assistant_platform.storage.db import init_assistant_db


def test_seed_is_idempotent():
    Session = init_assistant_db("sqlite://", team_id="team-seed")
    session = Session()
    seed_phase1_capabilities(session, "team-seed")
    seed_phase1_capabilities(session, "team-seed")
    session.commit()

    assert session.scalar(select(func.count()).select_from(CapabilityDefinitionRow)) == len(
        CAPABILITY_OPERATIONS
    )
    assert session.scalar(select(func.count()).select_from(CapabilityVersionRow)) == len(
        CAPABILITY_OPERATIONS
    )
    assert session.scalar(select(func.count()).select_from(CapabilityPackRow)) == 2
    assert session.scalar(select(func.count()).select_from(CapabilityAssignmentRow)) == 3


def test_init_assistant_db_seeds_with_default_team():
    Session = init_assistant_db("sqlite://")
    session = Session()

    assert session.scalar(select(func.count()).select_from(CapabilityDefinitionRow)) == len(
        CAPABILITY_OPERATIONS
    )
    packs = session.scalars(select(CapabilityPackRow)).all()
    assert {p.team_id for p in packs} == {"default"}


def test_packs_contain_expected_keys():
    Session = init_assistant_db("sqlite://", team_id="team-packs")
    session = Session()

    packs = {p.key: p.id for p in session.scalars(select(CapabilityPackRow)).all()}
    assert set(packs) == {"cursor_self_service", "assistant_owner"}

    def pack_keys(pack_key: str) -> set[str]:
        pack_id = packs[pack_key]
        rows = session.scalars(
            select(CapabilityPackItemRow).where(CapabilityPackItemRow.pack_id == pack_id)
        ).all()
        return {r.capability_key for r in rows}

    assert pack_keys("cursor_self_service") == set(SELF_SERVICE_KEYS)
    owner_expected = set(dict.fromkeys(SELF_SERVICE_KEYS + OWNER_EXTRA_KEYS))
    assert pack_keys("assistant_owner") == owner_expected


def test_assignments_seed_team_default_and_owner_role():
    Session = init_assistant_db("sqlite://", team_id="team-assign")
    session = Session()

    packs = {p.key: p.id for p in session.scalars(select(CapabilityPackRow)).all()}
    assignments = session.scalars(select(CapabilityAssignmentRow)).all()

    team_default = next(a for a in assignments if a.scope_type == "team_default")
    assert team_default.scope_id == ""
    assert team_default.pack_id == packs["cursor_self_service"]
    assert team_default.capability_key is None

    owner_role = next(
        a for a in assignments if a.scope_type == "role_pack" and a.scope_id == "owner"
    )
    assert owner_role.pack_id == packs["assistant_owner"]


def _allow_duplicate_pack_keys(session) -> None:
    """Recreate packs table without unique constraint (legacy dirty DB simulation)."""
    session.execute(text("PRAGMA foreign_keys=OFF"))
    session.execute(
        text(
            """
            CREATE TABLE ap_capability_packs_legacy (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                team_id VARCHAR(36) NOT NULL,
                "key" VARCHAR(64) NOT NULL,
                display_name VARCHAR(128) NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
    )
    session.execute(text("INSERT INTO ap_capability_packs_legacy SELECT * FROM ap_capability_packs"))
    session.execute(text("DROP TABLE ap_capability_packs"))
    session.execute(text("ALTER TABLE ap_capability_packs_legacy RENAME TO ap_capability_packs"))
    session.execute(
        text("CREATE INDEX ix_ap_capability_packs_team_id ON ap_capability_packs (team_id)")
    )
    session.execute(text("PRAGMA foreign_keys=ON"))
    session.commit()


def test_seed_dedupes_duplicate_packs():
    Session = init_assistant_db("sqlite://", team_id="team-dup")
    session = Session()
    _allow_duplicate_pack_keys(session)

    seeded = session.scalar(
        select(CapabilityPackRow).where(
            CapabilityPackRow.team_id == "team-dup",
            CapabilityPackRow.key == "cursor_self_service",
        )
    )
    assert seeded is not None
    seeded_item_count = session.scalar(
        select(func.count())
        .select_from(CapabilityPackItemRow)
        .where(CapabilityPackItemRow.pack_id == seeded.id)
    )
    assert seeded_item_count == len(SELF_SERVICE_KEYS)

    thin = CapabilityPackRow(
        team_id="team-dup", key="cursor_self_service", display_name="old thin"
    )
    session.add(thin)
    session.flush()
    session.add(
        CapabilityPackItemRow(
            pack_id=thin.id,
            capability_key="quota.self.read",
            capability_version="1",
        )
    )
    session.add(
        CapabilityAssignmentRow(
            team_id="team-dup",
            scope_type="team_default",
            scope_id="",
            pack_id=thin.id,
        )
    )
    session.commit()

    duplicate_packs = session.scalars(
        select(CapabilityPackRow).where(
            CapabilityPackRow.team_id == "team-dup",
            CapabilityPackRow.key == "cursor_self_service",
        )
    ).all()
    assert len(duplicate_packs) == 2

    seed_phase1_capabilities(session, "team-dup")
    session.commit()

    packs = session.scalars(
        select(CapabilityPackRow).where(
            CapabilityPackRow.team_id == "team-dup",
            CapabilityPackRow.key == "cursor_self_service",
        )
    ).all()
    assert len(packs) == 1
    items = {
        i.capability_key
        for i in session.scalars(
            select(CapabilityPackItemRow).where(CapabilityPackItemRow.pack_id == packs[0].id)
        ).all()
    }
    assert set(SELF_SERVICE_KEYS) <= items

    assigns = session.scalars(
        select(CapabilityAssignmentRow).where(
            CapabilityAssignmentRow.team_id == "team-dup",
            CapabilityAssignmentRow.scope_type == "team_default",
        )
    ).all()
    assert len(assigns) == 1
    assert assigns[0].pack_id == packs[0].id


def test_seed_counts_match_catalog():
    Session = init_assistant_db("sqlite://", team_id="team-full")
    session = Session()
    assert session.scalar(select(func.count()).select_from(CapabilityDefinitionRow)) == len(
        CAPABILITY_OPERATIONS
    )
    assert session.scalar(select(func.count()).select_from(CapabilityPackRow)) == 2
    assert session.scalar(select(func.count()).select_from(CapabilityAssignmentRow)) == 3
