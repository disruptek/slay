"""
Multi-tier storage system for slay.

Automatically selects optimal storage tier based on data size:
- Inline: < 1KB (embedded directly in reference)
- S3: >= 1KB (Amazon S3 object storage)
- Future: Cache, CDN, etc.
"""

import os
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from slay.core.reference import Reference
from slay.core.serializer import TypeInfo, deserialize
from slay.exceptions import StorageError
from slay.observability.logger import get_logger
from slay.observability.trace_context import trace_operation

logger = get_logger()

# Tier thresholds
INLINE_THRESHOLD = 1024  # 1KB
S3_THRESHOLD = 4_000_000  # 4MB (safe margin for Lambda payload)


def store_data(
    data: bytes, hash_val: str, type_info: TypeInfo, size: int
) -> Reference:
    """
    Store data in appropriate tier and return reference.

    Tier selection:
    - Inline (< 1KB): Embed directly in reference
    - S3 (>= 1KB): Store in S3 bucket

    Args:
        data: Serialized bytes
        hash_val: SHA256 hash
        type_info: Type metadata
        size: Size in bytes

    Returns:
        Reference to stored data

    Raises:
        StorageError: If storage fails
    """
    with trace_operation("store_data", size_bytes=size):
        if size < INLINE_THRESHOLD:
            logger.debug("store_inline", hash=hash_val[:8], size_bytes=size)
            return Reference(
                hash=hash_val,
                size=size,
                storage_tier="inline",
                type_info=type_info,
                inline_data=data,
                s3_key=None,
            )
        else:
            # Store in S3
            logger.debug("store_s3", hash=hash_val[:8], size_bytes=size)
            with trace_operation("s3_put", size_bytes=size):
                s3_key = _store_s3(data, hash_val)
                logger.debug("s3_put_complete", hash=hash_val[:8], s3_key=s3_key)
            return Reference(
                hash=hash_val,
                size=size,
                storage_tier="s3",
                type_info=type_info,
                inline_data=None,
                s3_key=s3_key,
            )


def resolve_reference(ref: Reference):
    """
    Fetch and deserialize data from reference.

    Args:
        ref: Reference to resolve

    Returns:
        Deserialized object

    Raises:
        StorageError: If fetching fails
        SerializationError: If deserialization fails
    """
    with trace_operation("resolve_reference", tier=ref.storage_tier, hash=ref.hash[:8]):
        if ref.storage_tier == "inline":
            if ref.inline_data is None:
                raise StorageError(f"Inline reference {ref.hash[:8]} has no data")
            data = ref.inline_data
            logger.debug("resolve_inline", hash=ref.hash[:8])
        elif ref.storage_tier == "s3":
            if ref.s3_key is None:
                raise StorageError(f"S3 reference {ref.hash[:8]} has no key")
            logger.debug("resolve_s3_start", hash=ref.hash[:8], s3_key=ref.s3_key)
            with trace_operation("s3_get", hash=ref.hash[:8]):
                data = _fetch_s3(ref.s3_key)
            logger.debug("resolve_s3_complete", hash=ref.hash[:8])
        else:
            raise StorageError(f"Unknown storage tier: {ref.storage_tier}")

        with trace_operation("deserialize"):
            obj = deserialize(data, ref.type_info)
        
        logger.debug("resolve_complete", hash=ref.hash[:8], type=ref.type_info.type_name)
        return obj


def load_many(refs: list[Reference], max_workers: int = 10) -> list:
    """
    Fetch and deserialize multiple references in parallel.

    Uses ThreadPoolExecutor for concurrent I/O. Falls back to sequential
    loading if all references have inline data or on errors.

    Args:
        refs: List of References to resolve
        max_workers: Maximum concurrent fetch threads (default 10)

    Returns:
        List of deserialized objects in same order as input

    Raises:
        StorageError: If any fetch fails
        SerializationError: If deserialization fails

    Example:
        >>> refs = [slay.put(f"data{i}") for i in range(100)]
        >>> data_list = load_many(refs, max_workers=20)
    """
    with trace_operation("load_many", ref_count=len(refs), max_workers=max_workers):
        logger.debug("load_many_start", ref_count=len(refs), max_workers=max_workers)
        
        if not refs:
            logger.debug("load_many_empty")
            return []

        # Check if all are inline (common case - no I/O needed)
        if all(ref.storage_tier == "inline" for ref in refs):
            logger.debug("load_many_all_inline", ref_count=len(refs))
            return [resolve_reference(ref) for ref in refs]

        # Use ThreadPoolExecutor for parallel fetching
        results = [None] * len(refs)
        errors = {}
        
        inline_count = sum(1 for r in refs if r.storage_tier == "inline")
        s3_count = len(refs) - inline_count
        logger.debug("load_many_mix", inline=inline_count, s3=s3_count)

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all fetch tasks
                future_to_index = {
                    executor.submit(resolve_reference, ref): idx for idx, ref in enumerate(refs)
                }

                # Collect results as they complete
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    try:
                        results[idx] = future.result()
                    except Exception as e:
                        errors[idx] = e

            # Check for errors
            if errors:
                first_error_idx = min(errors.keys())
                logger.error("load_many_failed", error_count=len(errors), first_error_idx=first_error_idx)
                raise errors[first_error_idx]

            logger.debug("load_many_complete", ref_count=len(refs))
            return results

        except Exception as e:
            raise StorageError(f"Parallel load failed: {e}") from e


# ============================================================================
# S3 Operations
# ============================================================================


def _store_s3(data: bytes, hash_val: str) -> str:
    """
    Store data in S3 (or local filesystem if bucket env var is a path).

    Args:
        data: Bytes to store
        hash_val: Content hash (for key)

    Returns:
        S3 object key or filesystem path

    Raises:
        StorageError: If storage operation fails
    """
    bucket = _get_storage_bucket()
    try:
        # Shard by first 2 chars for better S3 performance
        key = f"refs/{hash_val[:2]}/{hash_val}.bin"

        # Check if bucket is actually a local path (for testing)
        if os.path.exists(bucket) and os.path.isdir(bucket):
            # Use local filesystem
            filepath = os.path.join(bucket, key)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(data)
            return key

        # Use S3
        import boto3

        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
            Metadata={"hash": hash_val, "size": str(len(data))},
        )
        return key
    except Exception as e:
        raise StorageError(f"Failed to store in S3: {e}") from e


def _fetch_s3(key: str) -> bytes:
    """
    Fetch data from S3 (or local filesystem if bucket env var is a path).

    Args:
        key: S3 object key or filesystem path

    Returns:
        Object bytes

    Raises:
        StorageError: If fetch fails
    """
    bucket = _get_storage_bucket()
    try:
        # Check if bucket is actually a local path (for testing)
        if os.path.exists(bucket) and os.path.isdir(bucket):
            # Use local filesystem
            filepath = os.path.join(bucket, key)
            with open(filepath, "rb") as f:
                return f.read()

        # Use S3
        import boto3

        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except Exception as e:
        raise StorageError(f"Failed to fetch from S3: {e}") from e


def _get_storage_bucket() -> str:
    """
    Get the store for values of 1 KB or more from SLAY_STORAGE_BUCKET.

    The value is an S3 bucket name (the StorageBucket output of
    SlayStorageStack) or an existing local directory. There is no default.

    Returns:
        S3 bucket name or directory path

    Raises:
        StorageError: If SLAY_STORAGE_BUCKET is not set; the message says
            what to set it to
    """
    if bucket := os.getenv("SLAY_STORAGE_BUCKET"):
        return bucket

    raise StorageError(
        "SLAY_STORAGE_BUCKET is not set. Values of 1 KB or more need it: set it "
        "to the S3 bucket name (the StorageBucket output of SlayStorageStack) "
        "or to an existing local directory."
    )
