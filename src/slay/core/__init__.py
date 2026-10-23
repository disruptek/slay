"""Core abstractions for slay distributed computing"""

from slay.core.reference import Reference, create_reference
from slay.core.serializer import TypeInfo, serialize, deserialize, get_type_info
from slay.core.storage import store_data, resolve_reference
from slay.core.context import get_backend, set_backend, get_context

__all__ = [
    "Reference",
    "create_reference",
    "TypeInfo",
    "serialize",
    "deserialize",
    "get_type_info",
    "store_data",
    "resolve_reference",
    "get_backend",
    "set_backend",
    "get_context",
]
