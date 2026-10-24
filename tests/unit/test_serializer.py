"""
Unit tests for serialization system.
"""

import pytest
import json
import sys

import slay
from slay.core.serializer import (
    TypeInfo,
    serialize,
    deserialize,
    get_type_info,
    is_json_serializable,
)
from slay.exceptions import SerializationError


class TestSerializationBasics:
    """Tests for basic serialization"""

    def test_serialize_dict(self):
        """Can serialize dictionaries"""
        obj = {"key": "value", "num": 42}
        data = serialize(obj)

        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_serialize_list(self):
        """Can serialize lists"""
        obj = [1, 2, 3, "four", {"five": 5}]
        data = serialize(obj)

        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_serialize_function(self):
        """Can serialize functions"""

        def my_func(x):
            return x * 2

        data = serialize(my_func)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_serialize_lambda(self):
        """Can serialize lambda functions"""
        obj = lambda x: x * 2
        data = serialize(obj)

        assert isinstance(data, bytes)
        assert len(data) > 0


class TestDeserialization:
    """Tests for deserialization"""

    def test_roundtrip_dict(self):
        """Serialize and deserialize dict"""
        obj = {"key": "value", "nested": {"inner": 123}}
        type_info = get_type_info(obj)
        data = serialize(obj)
        loaded = deserialize(data, type_info)

        assert loaded == obj

    def test_roundtrip_list(self):
        """Serialize and deserialize list"""
        obj = [1, 2.5, "three", None, True]
        type_info = get_type_info(obj)
        data = serialize(obj)
        loaded = deserialize(data, type_info)

        assert loaded == obj

    def test_roundtrip_function(self):
        """Serialize and deserialize function"""

        def my_func(x, y):
            return x + y

        type_info = get_type_info(my_func)
        data = serialize(my_func)
        loaded = deserialize(data, type_info)

        # Functions should be callable
        assert loaded(5, 3) == 8


class TestJSONDetection:
    """Tests for JSON serializable detection"""

    def test_json_serializable_dict(self):
        """Dicts with simple values are JSON serializable"""
        assert is_json_serializable({"key": "value"})

    def test_json_serializable_list(self):
        """Lists with simple values are JSON serializable"""
        assert is_json_serializable([1, 2, 3])

    def test_json_serializable_primitives(self):
        """Primitives are JSON serializable"""
        assert is_json_serializable(42)
        assert is_json_serializable("string")
        assert is_json_serializable(True)
        assert is_json_serializable(None)

    def test_not_json_serializable_function(self):
        """Functions are not JSON serializable"""

        def func():
            pass

        assert not is_json_serializable(func)

    def test_not_json_serializable_bytes(self):
        """Bytes are not JSON serializable"""
        assert not is_json_serializable(b"bytes")


class TestTypeInfo:
    """Tests for type information extraction"""

    def test_type_info_json(self):
        """JSON-serializable object should have json format"""
        type_info = get_type_info({"key": "value"})
        assert type_info.format == "json"

    def test_type_info_dill_for_function(self):
        """Functions should use dill format"""

        def my_func():
            pass

        type_info = get_type_info(my_func)
        assert type_info.format == "dill"

    def test_type_info_preserves_type_name(self):
        """TypeInfo should preserve type name"""
        type_info = get_type_info({"a": 1})
        assert "dict" in type_info.type_name.lower()


def _roundtrip(obj):
    """Serialize and deserialize obj the way storage does"""
    return deserialize(serialize(obj), get_type_info(obj))


class TestExactRoundTrip:
    """JSON is chosen only when it gives back the same types"""

    def test_json_accepts_plain_nested_values(self):
        """Exact JSON types at every level are JSON serializable"""
        assert is_json_serializable([1, {"a": [None, 1.5, True, "s"]}])

    def test_tuple_is_not_json(self):
        """JSON would turn a tuple into a list"""
        assert not is_json_serializable((1, 2))
        assert get_type_info((1, 2)).format == "dill"

    def test_tuple_nested_in_list_is_not_json(self):
        """A tuple anywhere in the value rules out JSON"""
        assert not is_json_serializable([1, (2, 3)])

    def test_non_str_key_is_not_json(self):
        """JSON would turn an int key into a str key"""
        assert not is_json_serializable({1: "a"})

    def test_dict_subclass_is_not_json(self):
        """JSON would turn an OrderedDict into a dict"""
        from collections import OrderedDict

        assert not is_json_serializable(OrderedDict(a=1))

    def test_tuple_roundtrips_as_tuple(self):
        """A tuple comes back as a tuple"""
        loaded = _roundtrip((1, 2))
        assert loaded == (1, 2)
        assert type(loaded) is tuple

    def test_int_keys_roundtrip(self):
        """Int keys come back as int keys"""
        assert _roundtrip({1: "a", 2: "b"}) == {1: "a", 2: "b"}

    def test_nested_tuple_roundtrips(self):
        """A tuple inside a list comes back as a tuple"""
        loaded = _roundtrip([1, (2, 3)])
        assert loaded == [1, (2, 3)]
        assert type(loaded[1]) is tuple

    def test_ordered_dict_roundtrips(self):
        """An OrderedDict comes back as an OrderedDict"""
        from collections import OrderedDict

        loaded = _roundtrip(OrderedDict(b=1, a=2))
        assert type(loaded) is OrderedDict
        assert list(loaded) == ["b", "a"]

    def test_cyclic_list_falls_back_to_dill(self):
        """A list that contains itself is not JSON, and dill keeps the cycle"""
        cyclic = [1]
        cyclic.append(cyclic)

        assert not is_json_serializable(cyclic)
        loaded = _roundtrip(cyclic)
        assert loaded[1] is loaded


class TestPythonVersion:
    """dill values record and check the Python minor version"""

    CURRENT = "{}.{}".format(*sys.version_info[:2])

    @staticmethod
    def _function_bytes_and_info(python):
        """Real dill bytes of a function, with metadata claiming `python`"""
        def add(a, b):
            return a + b

        info = get_type_info(add)
        return serialize(add), TypeInfo(
            format=info.format,
            type_name=info.type_name,
            module=info.module,
            metadata={} if python is None else {"python": python},
        )

    def test_dill_records_python_version(self):
        """get_type_info() records the minor version for dill values"""
        assert get_type_info(lambda: None).metadata == {"python": self.CURRENT}

    def test_json_has_no_python_version(self):
        """JSON values do not depend on the Python version"""
        assert get_type_info({"a": 1}).metadata == {}

    def test_other_version_is_rejected(self):
        """A dill value from another minor version raises a clear error"""
        data, info = self._function_bytes_and_info("2.7")

        with pytest.raises(SerializationError) as excinfo:
            deserialize(data, info)

        message = str(excinfo.value)
        assert "Python 2.7" in message
        assert f"Python {self.CURRENT}" in message

    def test_same_version_loads(self):
        """A dill value from this minor version loads"""
        data, info = self._function_bytes_and_info(self.CURRENT)

        assert deserialize(data, info)(2, 3) == 5

    def test_value_without_version_loads(self):
        """A dill value written before the field existed still loads"""
        data, info = self._function_bytes_and_info(None)

        assert deserialize(data, info)(2, 3) == 5


class TestSlayModules:
    """dill stores slay and its submodules by name, not by value"""

    def test_function_that_names_slay_round_trips(self):
        """slay's module state does not pickle, so by value this fails"""

        def describe(x):
            return f"{slay.__name__}:{x}"

        assert _roundtrip(describe)(1) == "slay:1"

    def test_loaded_function_uses_the_imported_module(self):
        def module():
            return slay

        assert _roundtrip(module)() is sys.modules["slay"]

    def test_submodule_by_name(self):
        import slay.core.storage as storage

        def module():
            return storage

        assert _roundtrip(module)() is sys.modules["slay.core.storage"]
