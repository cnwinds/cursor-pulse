from __future__ import annotations

import logging
from pathlib import Path

from pulse.config import ObjectStorageConfig

logger = logging.getLogger(__name__)


def upload_file(config: ObjectStorageConfig, local_path: Path, object_key: str) -> str | None:
    """上传原始文件到 S3 兼容对象存储，返回 s3:// URI。"""
    if not config.enabled or not config.bucket:
        return None
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("请安装对象存储依赖：pip install -e '.[s3]'") from exc

    client_kwargs: dict = {}
    if config.endpoint_url:
        client_kwargs["endpoint_url"] = config.endpoint_url
    if config.region:
        client_kwargs["region_name"] = config.region

    client = boto3.client(
        "s3",
        aws_access_key_id=config.access_key or None,
        aws_secret_access_key=config.secret_key or None,
        **client_kwargs,
    )
    key = f"{config.prefix.strip('/')}/{object_key}".lstrip("/")
    client.upload_file(str(local_path), config.bucket, key)
    uri = f"s3://{config.bucket}/{key}"
    logger.info("Uploaded %s -> %s", local_path, uri)
    return uri


def archive_raw_file(
    config: ObjectStorageConfig,
    local_path: Path,
    *,
    team_slug: str,
    member_id: str,
    period: str,
    filename: str,
) -> str | None:
    object_key = f"{team_slug}/{member_id}/{period}/{filename}"
    return upload_file(config, local_path, object_key)
