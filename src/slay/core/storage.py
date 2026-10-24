"""
Multi-tier storage system for slay.

Automatically selects optimal storage tier based on data size:
- Inline: < 1KB (embedded directly in reference)
- S3: >= 1KB (Amazon S3 object storage)
- Future: Cache, CDN, etc.
"""

import os
from contextlib import contextmanager
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
# Object Store (S3 or a directory)
# ============================================================================


def write_object(key: str, data: bytes, metadata: Optional[dict[str, str]] = None) -> None:
    """
    Write bytes under a key in SLAY_STORAGE_BUCKET (S3 or a directory).

    Args:
        key: Object key, for example "cache/ab/abc....json"
        data: Bytes to write
        metadata: S3 object metadata; a directory ignores it

    Raises:
        StorageError: If SLAY_STORAGE_BUCKET is not set, or the write fails
    """
    open_store().put(key, data, metadata)


def read_object(key: str) -> Optional[bytes]:
    """
    Read the bytes under a key in SLAY_STORAGE_BUCKET.

    Returns:
        The bytes, or None if the key does not exist

    Raises:
        StorageError: If SLAY_STORAGE_BUCKET is not set, or the read fails
            for another reason (S3 reports a missing key as access denied
            to a caller without s3:ListBucket)
    """
    return open_store().get(key)


def object_exists(key: str) -> bool:
    """
    Check whether a key exists in SLAY_STORAGE_BUCKET, without reading it.

    Raises:
        StorageError: As read_object
    """
    return open_store().exists(key)


def _store_s3(data: bytes, hash_val: str) -> str:
    """
    Store a value's bytes under refs/, sharded by the first 2 chars of hash.

    Args:
        data: Bytes to store
        hash_val: Content hash (for key)

    Returns:
        The object key

    Raises:
        StorageError: If storage operation fails
    """
    key = f"refs/{hash_val[:2]}/{hash_val}.bin"
    write_object(key, data, {"hash": hash_val, "size": str(len(data))})
    return key


def _fetch_s3(key: str) -> bytes:
    """
    Fetch a value's bytes by the key _store_s3 returned.

    Raises:
        StorageError: If the key does not exist, or the fetch fails
    """
    data = read_object(key)
    if data is None:
        raise StorageError(f"Failed to fetch {key}: it does not exist")
    return data


@contextmanager
def _storage_errors(action: str, key: str):
    """Re-raise any failure but StorageError as StorageError"""
    try:
        yield
    except StorageError:
        raise
    except Exception as e:
        raise StorageError(f"Failed to {action} {key}: {e}") from e


class _Store:
    """
    Objects by key in one location. put, get and exists raise StorageError;
    a subclass implements _put, _get (None if missing) and _exists.
    """

    def put(self, key: str, data: bytes, metadata: Optional[dict[str, str]] = None) -> None:
        with _storage_errors("write", key):
            self._put(key, data, metadata or {})

    def get(self, key: str) -> Optional[bytes]:
        with _storage_errors("read", key):
            return self._get(key)

    def exists(self, key: str) -> bool:
        with _storage_errors("check", key):
            return self._exists(key)


class _DirectoryStore(_Store):
    """Objects as files under a local directory, for tests and local use"""

    def __init__(self, root: str):
        self.root = root

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def _put(self, key: str, data: bytes, metadata: dict[str, str]) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def _get(self, key: str) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def _exists(self, key: str) -> bool:
        return os.path.isfile(self._path(key))


class _S3Store(_Store):
    """Objects in an S3 bucket; one client, which threads may share"""

    _MISSING = ("404", "NoSuchKey", "NotFound")

    def __init__(self, bucket: str):
        import boto3

        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def _put(self, key: str, data: bytes, metadata: dict[str, str]) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
            Metadata=metadata,
        )

    def _get(self, key: str) -> Optional[bytes]:
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except self.s3.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in self._MISSING:
                return None
            raise
        return response["Body"].read()

    def _exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
        except self.s3.exceptions.ClientError as e:
            if e.response.get("Error", {}).get("Code") in self._MISSING:
                return False
            raise
        return True


def open_store() -> _Store:
    """
    The store SLAY_STORAGE_BUCKET names: a directory if one exists there,
    else an S3 bucket. Open one store for a batch of threaded calls:
    creating boto3 clients from several threads at once is not safe.

    Raises:
        StorageError: If SLAY_STORAGE_BUCKET is not set
    """
    location = _get_storage_bucket()
    if os.path.isdir(location):
        return _DirectoryStore(location)
    return _S3Store(location)


def open_bucket() -> _S3Store:
    """
    The S3 bucket SLAY_STORAGE_BUCKET names, for objects that an AWS
    service reads or writes itself (a Step Functions ItemReader, for one).

    Raises:
        StorageError: If SLAY_STORAGE_BUCKET is not set, or names a
            directory
    """
    location = _get_storage_bucket()
    if os.path.isdir(location):
        raise StorageError(
            f"SLAY_STORAGE_BUCKET names the directory {location}, but AWS reads "
            "and writes these objects itself: set it to the S3 bucket name (the "
            "StorageBucket output of SlayStorageStack)."
        )
    return _S3Store(location)


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
