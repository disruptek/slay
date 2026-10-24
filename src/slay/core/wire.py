"""
JSON wire format for references and calls.

LambdaBackend encodes each call with encode_call(), or a fan-out's items
with encode_arguments(). runtime/handler.py decodes a call with
decode_call() and sends its result back as ref_to_dict(). Both sides
import this module, so the format has one definition.
"""

from typing import Any

from slay.core.reference import Reference
from slay.core.serializer import TypeInfo


def ref_to_dict(ref: Reference) -> dict[str, Any]:
    """Convert a Reference to a JSON-serializable dict"""
    return {
        "hash": ref.hash,
        "size": ref.size,
        "storage_tier": ref.storage_tier,
        "type_info": ref.type_info.to_dict(),
        "inline_data": ref.inline_data.hex() if ref.inline_data is not None else None,
        "s3_key": ref.s3_key,
    }


def dict_to_ref(data: dict[str, Any]) -> Reference:
    """Reconstruct a Reference from ref_to_dict() output"""
    inline_hex = data["inline_data"]
    return Reference(
        hash=data["hash"],
        size=data["size"],
        storage_tier=data["storage_tier"],
        type_info=TypeInfo(**data["type_info"]),
        inline_data=bytes.fromhex(inline_hex) if inline_hex is not None else None,
        s3_key=data["s3_key"],
    )


def encode_arguments(args: list[Reference], kwargs: dict[str, Reference]) -> dict[str, Any]:
    """Encode the arguments of one call: an item of a fan-out's items file"""
    return {
        "args_refs": [ref_to_dict(ref) for ref in args],
        "kwargs_refs": {name: ref_to_dict(ref) for name, ref in kwargs.items()},
    }


def encode_call(
    func_ref: Reference, args: list[Reference], kwargs: dict[str, Reference]
) -> dict[str, Any]:
    """Encode one function call as a JSON-serializable event"""
    return {"func_ref": ref_to_dict(func_ref), **encode_arguments(args, kwargs)}


def decode_call(
    event: dict[str, Any],
) -> tuple[Reference, list[Reference], dict[str, Reference]]:
    """Decode an encode_call() event into (func_ref, args, kwargs)"""
    return (
        dict_to_ref(event["func_ref"]),
        [dict_to_ref(ref) for ref in event["args_refs"]],
        {name: dict_to_ref(ref) for name, ref in event["kwargs_refs"].items()},
    )
