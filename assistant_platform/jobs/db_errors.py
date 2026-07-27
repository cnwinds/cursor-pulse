"""Helpers for classifying SQLite / SQLAlchemy lock failures."""

from __future__ import annotations

from sqlalchemy.exc import OperationalError, PendingRollbackError


def is_retryable_db_lock_error(exc: BaseException | None) -> bool:
    """Return True when the failure is transient writer contention."""
    if exc is None:
        return False
    if isinstance(exc, PendingRollbackError):
        cause = exc.__cause__ or getattr(exc, "orig", None)
        if isinstance(cause, BaseException) and is_retryable_db_lock_error(cause):
            return True
        return "database is locked" in str(exc).lower()
    if isinstance(exc, OperationalError):
        text = str(exc).lower()
        if "database is locked" in text or "database is busy" in text:
            return True
        orig = getattr(exc, "orig", None)
        if orig is not None and "database is locked" in str(orig).lower():
            return True
    # Walk causes for wrapped errors from worker / ORM.
    cause = exc.__cause__
    if isinstance(cause, BaseException) and cause is not exc:
        return is_retryable_db_lock_error(cause)
    return False
