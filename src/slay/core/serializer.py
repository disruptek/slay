"""
Serialization system using dill with automatic format detection.

Supports:
- JSON for objects that JSON returns unchanged (see is_json_serializable)
- Parquet for pandas DataFrames
- NPY for numpy arrays
- Dill for everything else (functions, classes, custom objects)
"""

import importlib
import io
import json
import sys
import dill
from dataclasses import dataclass, asdict
from types import ModuleType
from typing import Any, Optional

from slay.exceptions import SerializationError


@dataclass(frozen=True)
class TypeInfo:
    """Metadata for deserialization"""

    format: str  # 'dill', 'json', 'parquet', 'numpy'
    type_name: str
    module: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict"""
        return asdict(self)


def serialize(obj: Any) -> bytes:
    """
    Serialize object to bytes, choosing optimal format based on type.

    Priorities:
    1. JSON (only if the round trip keeps every type)
    2. Parquet (for pandas DataFrames)
    3. NumPy NPY (for numpy arrays)
    4. Dill (everything else)

    Args:
        obj: Object to serialize

    Returns:
        Serialized bytes

    Raises:
        SerializationError: If serialization fails
    """
    try:
        format_str = _choose_format(obj)
        if format_str == "json":
            return json.dumps(obj).encode("utf-8")
        if format_str == "parquet":
            return _serialize_parquet(obj)
        if format_str == "numpy":
            return _serialize_numpy(obj)
        # dill handles functions, closures, classes, etc.
        return _dill_dumps(obj)
    except Exception as e:
        raise SerializationError(f"Failed to serialize {type(obj).__name__}: {e}") from e


def deserialize(data: bytes, type_info: TypeInfo) -> Any:
    """
    Deserialize bytes back to object using appropriate method.

    A dill value records the Python minor version that wrote it in
    type_info.metadata["python"]. dill bytecode does not load across minor
    versions, so a mismatch raises before dill is called.

    Args:
        data: Serialized bytes
        type_info: Type metadata from serialization

    Returns:
        Deserialized object

    Raises:
        SerializationError: If deserialization fails, or if a dill value
            was written by a different Python minor version
    """
    if type_info.format == "dill":
        written_by = type_info.metadata.get("python")
        if written_by is not None and written_by != _python_version():
            raise SerializationError(
                f"{type_info.type_name} was serialized by Python {written_by}, "
                f"but this is Python {_python_version()}. dill values load only "
                f"under the Python minor version that wrote them."
            )

    try:
        if type_info.format == "json":
            return json.loads(data.decode("utf-8"))
        elif type_info.format == "parquet":
            return _deserialize_parquet(data)
        elif type_info.format == "numpy":
            return _deserialize_numpy(data)
        else:  # dill
            return dill.loads(data)
    except Exception as e:
        raise SerializationError(
            f"Failed to deserialize {type_info.type_name}: {e}"
        ) from e


def get_type_info(obj: Any) -> TypeInfo:
    """
    Extract type metadata for deserialization.

    For the dill format, metadata["python"] records this interpreter's
    minor version (for example "3.13"). Other formats get empty metadata.

    Args:
        obj: Object to analyze

    Returns:
        TypeInfo metadata
    """
    obj_type = type(obj)
    format_str = _choose_format(obj)

    return TypeInfo(
        format=format_str,
        type_name=obj_type.__name__,
        module=obj_type.__module__,
        metadata={"python": _python_version()} if format_str == "dill" else {},
    )


class _Pickler(dill.Pickler):
    """
    dill's Pickler, except that it stores slay and its submodules by name.

    dill stores a module by value when its file is outside the standard
    library and site-packages: slay in src/ or in the Lambda's /var/task.
    slay's module state (a thread-local, for one) does not pickle, and
    every process that loads a slay value has slay to import.
    """

    def reducer_override(self, obj: Any) -> Any:
        if isinstance(obj, ModuleType) and obj.__name__.split(".")[0] == "slay":
            return importlib.import_module, (obj.__name__,)
        return NotImplemented


def _dill_dumps(obj: Any) -> bytes:
    """dill.dumps(obj, recurse=True), through _Pickler"""
    buffer = io.BytesIO()
    _Pickler(buffer, dill.settings["protocol"], recurse=True).dump(obj)
    return buffer.getvalue()


def _python_version() -> str:
    """This interpreter's minor version, for example 3.13"""
    return "{}.{}".format(*sys.version_info[:2])


# ============================================================================
# Helper Functions
# ============================================================================


def is_json_serializable(obj: Any) -> bool:
    """
    Check if JSON returns the object unchanged.

    True only when the object contains nothing but these exact types:
    None, bool, int, float, str, list, and dict with str keys. Subclasses,
    tuples, sets and non-str dict keys give False, because JSON would hand
    back a different type (a list for a tuple, a str key for an int key).

    Args:
        obj: Object to check

    Returns:
        True if the JSON round trip is exact, False otherwise
    """
    try:
        return _is_exact_json(obj)
    except RecursionError:
        # Cyclic or very deep structures; dill handles them
        return False


_JSON_SCALARS = (type(None), bool, int, float, str)


def _is_exact_json(obj: Any) -> bool:
    """Recursive part of is_json_serializable"""
    kind = type(obj)
    if kind in _JSON_SCALARS:
        return True
    if kind is list:
        return all(_is_exact_json(item) for item in obj)
    if kind is dict:
        return all(type(key) is str and _is_exact_json(value) for key, value in obj.items())
    return False


def _choose_format(obj: Any) -> str:
    """Pick the serialization format for obj (see serialize for priorities)"""
    if is_json_serializable(obj):
        return "json"
    if _is_pandas_dataframe(obj):
        return "parquet"
    if _is_numpy_array(obj):
        return "numpy"
    return "dill"


def _is_pandas_dataframe(obj: Any) -> bool:
    """Check if object is a pandas DataFrame"""
    return (
        type(obj).__module__.startswith("pandas")
        and type(obj).__name__ == "DataFrame"
    )


def _is_numpy_array(obj: Any) -> bool:
    """Check if object is a numpy array"""
    return type(obj).__module__ == "numpy" and type(obj).__name__ == "ndarray"


def _serialize_parquet(df: Any) -> bytes:
    """Serialize pandas DataFrame to Parquet"""
    import io

    buffer = io.BytesIO()
    df.to_parquet(buffer, index=True, compression="snappy")
    return buffer.getvalue()


def _deserialize_parquet(data: bytes) -> Any:
    """Deserialize pandas DataFrame from Parquet"""
    import io
    import pandas as pd

    return pd.read_parquet(io.BytesIO(data))


def _serialize_numpy(arr: Any) -> bytes:
    """Serialize numpy array to NPY format"""
    import io

    buffer = io.BytesIO()
    import numpy as np

    np.save(buffer, arr, allow_pickle=False)
    buffer.seek(0)
    return buffer.getvalue()


def _deserialize_numpy(data: bytes) -> Any:
    """Deserialize numpy array from NPY format"""
    import io
    import numpy as np

    return np.load(io.BytesIO(data), allow_pickle=False)
