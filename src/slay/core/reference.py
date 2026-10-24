"""
Content-addressed reference system.

Each piece of data (function, argument, result) becomes a Reference.
This enables:
- Automatic deduplication (same content = same hash)
- Location independence (don't care where it's stored)
- Lazy loading (fetch only when needed)
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from slay.core.serializer import serialize, get_type_info, TypeInfo


class _NotFetched:
    """Marker for a reference whose data has not been fetched yet"""

    def __repr__(self) -> str:
        return "<not fetched>"

    def __reduce__(self) -> str:
        # Pickle by name, so a copy is still the module's single marker
        return "_NOT_FETCHED"


_NOT_FETCHED = _NotFetched()


@dataclass(frozen=True)
class Reference:
    """
    Content-addressed reference to data or code.

    A Reference is an immutable identifier for any piece of data (functions,
    arguments, results, etc.). The hash is deterministic based on content,
    enabling automatic deduplication.
    
    References can be:
    - Called if they contain a function: result_ref = fn(arg)
    - Fetched if they contain data: data = ref.get()
    - Compared by content: ref1 == ref2 if same hash
    """

    hash: str  # SHA256 hex digest
    size: int  # Size in bytes
    storage_tier: str  # 'inline', 's3', 'cache'
    type_info: TypeInfo

    # Optional fields based on tier
    inline_data: Optional[bytes] = None  # For tier='inline'
    s3_key: Optional[str] = None  # For tier='s3'
    
    # Local cache (not frozen, used for caching fetched data)
    _cached_data: Any = field(default=_NOT_FETCHED, init=False, repr=False)

    def _is_cached(self) -> bool:
        """True once the data has been fetched (a cached None counts)"""
        return self._cached_data is not _NOT_FETCHED

    def _set_cached(self, data: Any) -> None:
        """Remember fetched data; the dataclass is frozen, so bypass it"""
        object.__setattr__(self, "_cached_data", data)

    @property
    def data(self) -> Any:
        """
        Lazy-load and deserialize the actual data.

        This property triggers fetching the data from storage and
        deserializing it back to its original form.

        Returns:
            The original object

        Raises:
            StorageError: If fetching fails
            SerializationError: If deserialization fails
        """
        from slay.core.storage import resolve_reference

        return resolve_reference(self)
    
    def get(self) -> Any:
        """
        Fetch and return the data from this reference.
        
        Results are cached locally after first fetch, so subsequent calls
        are fast (no remote fetches).
        
        Returns:
            The deserialized data
            
        Raises:
            StorageError: If fetching fails
            SerializationError: If deserialization fails
            
        Example:
            >>> ref = slay.put("hello")
            >>> data = ref.get()
            >>> print(data)
            "hello"
        """
        # Check local cache first
        if self._is_cached():
            return self._cached_data

        data = self.data
        self._set_cached(data)
        return data
    
    def __call__(self, *args, **kwargs) -> "Reference":
        """
        Execute this reference as a function remotely.

        Syntactic sugar for slay.run(fn, arg1, arg2, ...). Returns a
        Reference to the result; call .get() on it for the value. Reference
        arguments pass through without a download, as with run().

        Args:
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function

        Returns:
            Reference to the result

        Raises:
            Exception: The exception the function raised, as run() raises it
            RemoteExecutionError: If the backend fails outside the function

        Example:
            >>> fn = slay.put(lambda x: x + 1)
            >>> result_ref = fn(5)
            >>> print(result_ref.get())
            6
        """
        # Import here to avoid circular dependency
        from slay.api import run

        return run(self, *args, **kwargs)
    
    def __eq__(self, other) -> bool:
        """
        Compare references by content hash (content-addressed equality).
        
        Two references are equal if they have the same hash, meaning they
        contain the same data.
        
        Args:
            other: Another Reference to compare
            
        Returns:
            True if both references have the same hash
            
        Example:
            >>> ref1 = slay.put("data")
            >>> ref2 = slay.put("data")
            >>> print(ref1 == ref2)
            True  # Same content = equal
        """
        if not isinstance(other, Reference):
            return NotImplemented
        return self.hash == other.hash
    
    def __hash__(self) -> int:
        """Hash based on content hash for use in collections."""
        return hash(self.hash)

    def __repr__(self) -> str:
        return (
            f"Reference(hash={self.hash[:8]}..., size={self.size}, "
            f"tier={self.storage_tier})"
        )


def as_reference(value: Any) -> Reference:
    """A Reference passes through as it is; any other value is stored"""
    if isinstance(value, Reference):
        return value
    return create_reference(value)


def create_reference(obj: Any) -> Reference:
    """
    Create a content-addressed reference for any object.

    This is the primary entry point for putting data into the slay system.
    The object is serialized, hashed, and stored in the appropriate tier.

    Args:
        obj: Object to reference (function, data, etc.)

    Returns:
        Reference to the object

    Raises:
        SerializationError: If serialization fails
        StorageError: If storage fails

    Example:
        >>> def my_func(x):
        ...     return x * 2
        >>> func_ref = create_reference(my_func)
        >>> print(func_ref)
        Reference(hash=abc123..., size=156, tier=inline)
    """
    from slay.core.storage import store_data

    # Serialize
    data = serialize(obj)
    hash_val = hashlib.sha256(data).hexdigest()
    type_info = get_type_info(obj)
    size = len(data)

    # Store in appropriate tier and get reference
    ref = store_data(data, hash_val, type_info, size)

    return ref
