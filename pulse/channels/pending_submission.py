from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TTL = timedelta(hours=24)


@dataclass
class PendingUsageIngestion:
    dingtalk_user_id: str
    user_name: str
    channel: str
    source_type: str
    account_ids: list[str]
    created_at: str
    file_path: str | None = None
    raw_text: str | None = None
    extraction_confidence: float = 1.0
    status: str = "confirmed"
    extra_notes: list[str] | None = None
    notify_admins_review: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PendingUsageIngestion:
        source_type = data.get("source_type") or data.get("input_type", "manual_csv")
        return cls(
            dingtalk_user_id=data["dingtalk_user_id"],
            user_name=data.get("user_name", data["dingtalk_user_id"]),
            channel=data["channel"],
            source_type=source_type,
            account_ids=list(data["account_ids"]),
            created_at=data["created_at"],
            file_path=data.get("file_path"),
            raw_text=data.get("raw_text"),
            extraction_confidence=float(data.get("extraction_confidence", 1.0)),
            status=data.get("status", "confirmed"),
            extra_notes=list(data.get("extra_notes") or []),
            notify_admins_review=bool(data.get("notify_admins_review", False)),
        )


# Backward-compatible aliases
PendingUsageSubmission = PendingUsageIngestion


class PendingIngestionStore:
    def __init__(self, path: Path):
        self.path = path

    def _load_all(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_all(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, dingtalk_user_id: str) -> PendingUsageIngestion | None:
        raw = self._load_all().get(dingtalk_user_id)
        if not raw:
            return None
        pending = PendingUsageIngestion.from_dict(raw)
        created = datetime.fromisoformat(pending.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > DEFAULT_TTL:
            self.clear(dingtalk_user_id)
            return None
        return pending

    def save(self, pending: PendingUsageIngestion) -> None:
        data = self._load_all()
        data[pending.dingtalk_user_id] = pending.to_dict()
        self._save_all(data)

    def clear(self, dingtalk_user_id: str) -> None:
        data = self._load_all()
        if dingtalk_user_id in data:
            del data[dingtalk_user_id]
            self._save_all(data)


PendingSubmissionStore = PendingIngestionStore
