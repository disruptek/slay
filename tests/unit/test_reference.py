"""
Unit tests for reference system.
"""

import hashlib
import pytest
import slay
from slay.core.reference import create_reference, Reference


class TestReferenceCreation:
    """Tests for creating references"""

    def test_create_reference_simple(self, local_backend):
        """Test creating reference for simple data"""
        obj = {"key": "value"}
        ref = create_reference(obj)

        assert isinstance(ref, Reference)
        assert ref.size > 0
        assert len(ref.hash) == 64  # SHA256 hex

    def test_reference_hash_is_deterministic(self, local_backend):
        """Same data should always produce same hash"""
        obj = [1, 2, 3]

        ref1 = create_reference(obj)
        ref2 = create_reference(obj)

        assert ref1.hash == ref2.hash

    def test_different_data_different_hash(self, local_backend):
        """Different data should produce different hashes"""
        obj1 = [1, 2, 3]
        obj2 = [1, 2, 4]

        ref1 = create_reference(obj1)
        ref2 = create_reference(obj2)

        assert ref1.hash != ref2.hash

    def test_inline_tier_for_small_data(self, local_backend):
        """Small data should use inline tier"""
        small_obj = "hello"
        ref = create_reference(small_obj)

        assert ref.storage_tier == "inline"
        assert ref.inline_data is not None
        assert ref.s3_key is None

    def test_reference_repr(self, local_backend):
        """Reference should have readable repr"""
        ref = create_reference({"data": "test"})
        repr_str = repr(ref)

        assert "Reference" in repr_str
        assert "inline" in repr_str or "s3" in repr_str

    def test_reference_with_function(self, local_backend):
        """Can create reference for functions"""

        def my_func(x):
            return x * 2

        ref = create_reference(my_func)
        assert isinstance(ref, Reference)
        assert ref.size > 0


class TestReferenceLazyLoading:
    """Tests for lazy loading via .data property"""

    def test_lazy_load_simple_data(self, local_backend):
        """Data should be retrieved on .data access"""
        obj = {"data": list(range(100))}
        ref = create_reference(obj)

        # Reference created
        assert isinstance(ref, Reference)

        # Access .data to trigger fetch/deserialize
        loaded = ref.data
        assert loaded == obj

    def test_lazy_load_preserves_type(self, local_backend):
        """Lazy loading should preserve original type"""
        original = {"list": [1, 2, 3], "str": "test", "num": 42}
        ref = create_reference(original)
        loaded = ref.data

        assert loaded == original
        assert type(loaded) == dict


class TestReferenceTypeInfo:
    """Tests for type information preservation"""

    def test_type_info_for_dict(self, local_backend):
        """TypeInfo should be set correctly for dict"""
        ref = create_reference({"key": "value"})

        assert ref.type_info.format == "json"
        assert "dict" in ref.type_info.type_name

    def test_type_info_for_function(self, local_backend):
        """TypeInfo should be set for functions"""

        def my_func(x):
            return x

        ref = create_reference(my_func)

        assert ref.type_info.format == "dill"
        assert "function" in ref.type_info.type_name.lower()


class TestReferenceTypeFidelity:
    """References keep the type of the value they hold"""

    def test_tuple_and_list_differ(self, local_backend):
        """A tuple and a list with the same items are different content"""
        assert create_reference((1, 2)) != create_reference([1, 2])

    def test_tuple_reference_returns_tuple(self, local_backend):
        """put((1, 2)).get() returns a tuple"""
        assert type(slay.put((1, 2)).get()) is tuple
