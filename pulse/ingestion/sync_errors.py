from __future__ import annotations

import httpx


class SyncError(Exception):
    """Cursor 同步错误基类。"""


class RetryableSyncError(SyncError):
    """可退避重试（限流、5xx、网络）。"""


class FatalSyncError(SyncError):
    """不可自动重试（鉴权失败、账号无效）。"""


def classify_sync_error(exc: BaseException) -> SyncError:
    if isinstance(exc, SyncError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return FatalSyncError(str(exc))
        if status == 429 or status >= 500:
            return RetryableSyncError(str(exc))
        return FatalSyncError(str(exc))
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)):
        return RetryableSyncError(str(exc))
    if isinstance(exc, ValueError):
        return FatalSyncError(str(exc))
    return RetryableSyncError(str(exc))
