from datetime import datetime, timezone

from pulse.bot.pending_submission import PendingIngestionStore, PendingUsageIngestion


def test_pending_ingestion_roundtrip(tmp_path):
    store = PendingIngestionStore(tmp_path / "pending.json")
    pending = PendingUsageIngestion(
        dingtalk_user_id="u1",
        user_name="Alice",
        channel="private",
        source_type="manual_csv",
        account_ids=["acc-1"],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    store.save(pending)
    loaded = store.get("u1")
    assert loaded is not None
    assert loaded.source_type == "manual_csv"
    assert loaded.account_ids == ["acc-1"]
    store.clear("u1")
    assert store.get("u1") is None
