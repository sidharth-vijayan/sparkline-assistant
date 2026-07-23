"""
services/minio_service.py
──────────────────────────
MinIO client wrapper for raw document file storage.

Non-destructive versioning: files are NEVER deleted from MinIO.
When a new version of a document is uploaded, the old object key
stays in MinIO permanently for audit/history. The active version
is tracked in PostgreSQL (document_versions.is_active).

Object key format:
  documents/{document_id}/{version_id}/{original_filename}
"""

import io
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

import structlog
from minio import Minio
from minio.error import S3Error

from config.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


def _get_client() -> Minio:
    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password,
        secure=settings.minio_secure,
    )


def ensure_bucket_exists(bucket_name: str) -> None:
    """Create the bucket if it doesn't already exist."""
    client = _get_client()
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)
        logger.info("minio.bucket_created", bucket=bucket_name)


def upload_document(
    file_data: bytes | BinaryIO,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    original_filename: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload a document file to MinIO.

    Returns the object key (permanent, never deleted).
    Old versions remain under their own version_id prefix.
    """
    client = _get_client()
    bucket = settings.minio_bucket_documents
    ensure_bucket_exists(bucket)

    # Sanitize filename for safe object key usage
    safe_filename = Path(original_filename).name
    object_key = f"documents/{document_id}/{version_id}/{safe_filename}"

    if isinstance(file_data, bytes):
        data = io.BytesIO(file_data)
        length = len(file_data)
    else:
        # Seek to measure length if possible
        if hasattr(file_data, "seek") and hasattr(file_data, "tell"):
            file_data.seek(0, 2)
            length = file_data.tell()
            file_data.seek(0)
        else:
            length = -1  # Unknown length — MinIO will handle
        data = file_data

    client.put_object(
        bucket_name=bucket,
        object_name=object_key,
        data=data,
        length=length,
        content_type=content_type,
    )

    logger.info(
        "minio.upload_success",
        object_key=object_key,
        document_id=str(document_id),
        version_id=str(version_id),
    )
    return object_key


def download_document(object_key: str) -> bytes:
    """Download and return the raw bytes of a document from MinIO."""
    client = _get_client()
    bucket = settings.minio_bucket_documents
    try:
        response = client.get_object(bucket_name=bucket, object_name=object_key)
        data = response.read()
        response.close()
        response.release_conn()
        return data
    except S3Error as e:
        logger.error("minio.download_failed", object_key=object_key, error=str(e))
        raise FileNotFoundError(f"Object not found in MinIO: {object_key}") from e


def get_presigned_url(object_key: str, expires_seconds: int = 3600) -> str:
    """Generate a presigned download URL for a stored document."""
    from datetime import timedelta

    client = _get_client()
    bucket = settings.minio_bucket_documents
    url = client.presigned_get_object(
        bucket_name=bucket,
        object_name=object_key,
        expires=timedelta(seconds=expires_seconds),
    )
    return url


def stat_object(object_key: str) -> Optional[dict]:
    """Return object metadata (size, content_type, last_modified) or None if not found."""
    client = _get_client()
    bucket = settings.minio_bucket_documents
    try:
        stat = client.stat_object(bucket_name=bucket, object_name=object_key)
        return {
            "size": stat.size,
            "content_type": stat.content_type,
            "last_modified": stat.last_modified,
            "etag": stat.etag,
        }
    except S3Error:
        return None
