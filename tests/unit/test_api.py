"""
Unit tests for public API.

Tests for new API v2.0:
- put(data_or_func): Store and retrieve data/functions
- get(ref): Fetch from reference
- run(fn, *args, **kwargs): Execute function remotely (core operation)
- apply(fn, items): Apply function to multiple items in parallel
- get_many(refs): Fetch multiple references in parallel
"""

import pytest
import slay


class TestPutAndGet:
    """Tests for put() and get() - data storage and retrieval"""

    def test_put_simple_data(self, local_backend):
        """Can store and retrieve simple data"""
        data = {"key": "value", "count": 42}
        ref = slay.put(data)
        
        assert ref is not None
        retrieved = slay.get(ref)
        assert retrieved == data

    def test_put_and_ref_get(self, local_backend):
        """ref.get() is equivalent to slay.get(ref)"""
        data = [1, 2, 3, 4, 5]
        ref = slay.put(data)
        
        # Both ways should work
        assert ref.get() == data
        assert slay.get(ref) == data

    def test_put_string_data(self, local_backend):
        """Can store strings"""
        text = "hello world"
        ref = slay.put(text)
        assert ref.get() == text

    def test_put_complex_nested_data(self, local_backend):
        """Can store complex nested structures"""
        data = {
            "users": [
                {"name": "alice", "age": 30},
                {"name": "bob", "age": 25},
            ],
            "metadata": {"version": 1, "tags": ["test", "demo"]},
        }
        ref = slay.put(data)
        assert ref.get() == data

    def test_put_function(self, local_backend):
        """Can store functions"""
        def add(a, b):
            return a + b
        
        ref = slay.put(add)
        retrieved_fn = ref.get()
        
        # Should be callable
        assert retrieved_fn(5, 3) == 8

    def test_put_lambda(self, local_backend):
        """Can store lambda functions"""
        fn = slay.put(lambda x: x * 2)
        retrieved_fn = fn.get()
        assert retrieved_fn(21) == 42

    def test_get_caches_result(self, local_backend):
        """Multiple get() calls should use local cache"""
        data = {"expensive": "data"}
        ref = slay.put(data)
        
        # First call
        result1 = ref.get()
        # Second call should be cached
        result2 = ref.get()
        
        assert result1 == result2 == data
        # Verify cached data is set
        assert ref._cached_data is not None


class TestLocalCache:
    """ref.get() and get_many() fetch each reference at most once"""

    @staticmethod
    def _count_resolves(monkeypatch):
        """Count calls to storage.resolve_reference"""
        import slay.core.storage as storage

        calls = []
        original = storage.resolve_reference

        def counting(ref):
            calls.append(ref.hash)
            return original(ref)

        monkeypatch.setattr(storage, "resolve_reference", counting)
        return calls

    def test_get_caches_none(self, local_backend, monkeypatch):
        """A None value is cached like any other value"""
        ref = slay.put(None)
        calls = self._count_resolves(monkeypatch)

        assert ref.get() is None
        assert ref.get() is None
        assert len(calls) == 1

    def test_get_many_uses_cached_none(self, local_backend, monkeypatch):
        """get_many() does not fetch a reference whose None value is cached"""
        ref = slay.put(None)
        ref.get()
        calls = self._count_resolves(monkeypatch)

        assert slay.get_many([ref]) == [None]
        assert calls == []

    def test_unfetched_reference_survives_pickling(self, local_backend):
        """A pickled reference that was never fetched still fetches its value"""
        import dill

        ref = slay.put("value")
        copy = dill.loads(dill.dumps(ref))

        assert copy.get() == "value"


class TestReferenceCallable:
    """Tests for callable references - fn(arg) syntax

    fn(arg) is the same call as slay.run(fn, arg): it returns a Reference
    to the result, and .get() gives the value.
    """

    def test_call_reference_simple(self, local_backend):
        """Calling a function reference returns a Reference to the result"""
        fn = slay.put(lambda x: x + 1)
        result_ref = fn(5)

        assert isinstance(result_ref, slay.core.reference.Reference)
        assert result_ref.get() == 6

    def test_call_reference_multiple_args(self, local_backend):
        """Function references work with multiple arguments"""
        def add(a, b):
            return a + b

        fn = slay.put(add)
        assert fn(5, 3).get() == 8

    def test_call_reference_with_kwargs(self, local_backend):
        """Function references work with keyword arguments"""
        def multiply(x, factor=2):
            return x * factor

        fn = slay.put(multiply)
        assert fn(5, factor=3).get() == 15

    def test_call_equals_run(self, local_backend):
        """fn(arg) gives the same Reference as slay.run(fn, arg)"""
        fn = slay.put(lambda x: x * 2)

        assert fn(21) == slay.run(fn, 21)
        assert fn(21).get() == 42

    def test_call_reference_with_complex_args(self, local_backend):
        """Function references work with complex data arguments"""
        def process_data(data):
            return sum(data["values"]) * data["multiplier"]

        fn = slay.put(process_data)
        data = {"values": [1, 2, 3, 4], "multiplier": 2}
        assert fn(data).get() == 20


class TestReferenceEquality:
    """Tests for content-addressed equality"""

    def test_same_data_same_reference(self, local_backend):
        """References with same content should be equal"""
        ref1 = slay.put("data")
        ref2 = slay.put("data")
        
        assert ref1 == ref2

    def test_different_data_different_reference(self, local_backend):
        """References with different content should not be equal"""
        ref1 = slay.put("data1")
        ref2 = slay.put("data2")
        
        assert ref1 != ref2

    def test_complex_data_equality(self, local_backend):
        """Content-addressed equality works for complex data"""
        data = {"list": [1, 2, 3], "nested": {"key": "value"}}
        ref1 = slay.put(data)
        ref2 = slay.put({"list": [1, 2, 3], "nested": {"key": "value"}})
        
        assert ref1 == ref2

    def test_references_hashable(self, local_backend):
        """References can be used in sets/dicts"""
        ref1 = slay.put("data")
        ref2 = slay.put("data")
        ref3 = slay.put("other")
        
        # Should be usable in a set
        ref_set = {ref1, ref2, ref3}
        # Same content = same hash, so set has 2 elements
        assert len(ref_set) == 2

    def test_references_in_dict(self, local_backend):
        """References can be used as dictionary keys"""
        ref1 = slay.put("key")
        ref2 = slay.put("key")
        
        mapping = {ref1: "value"}
        # ref2 should work as key since it's equal to ref1
        assert mapping[ref2] == "value"


class TestRun:
    """Tests for run() - core execution primitive
    
    Important: run() returns a Reference, not the actual result.
    To get the result, use .get() or use the callable syntax fn(arg).
    """

    def test_run_simple_function(self, local_backend):
        """Can run a simple function"""
        def add(a, b):
            return a + b
        
        fn = slay.put(add)
        result_ref = slay.run(fn, 5, 3)
        assert result_ref.get() == 8

    def test_run_with_kwargs(self, local_backend):
        """run() works with keyword arguments"""
        def multiply(x, factor=2):
            return x * factor
        
        fn = slay.put(multiply)
        result_ref = slay.run(fn, 5, factor=3)
        assert result_ref.get() == 15

    def test_run_returns_reference(self, local_backend):
        """run() returns a Reference, not the result"""
        fn = slay.put(lambda x: x * 2)
        result_ref = slay.run(fn, 21)
        
        # Must be a Reference
        assert isinstance(result_ref, slay.core.reference.Reference)
        # Deserialize to get actual result
        assert result_ref.get() == 42

    def test_run_with_complex_data(self, local_backend):
        """run() works with complex data arguments"""
        def process_list(items):
            return [x * 2 for x in items]
        
        fn = slay.put(process_list)
        result_ref = slay.run(fn, [1, 2, 3, 4, 5])
        assert result_ref.get() == [2, 4, 6, 8, 10]

    def test_run_vs_call_syntax(self, local_backend):
        """run() and fn() both return a Reference to the result"""
        def double(x):
            return x * 2

        fn = slay.put(double)

        for result_ref in (slay.run(fn, 21), fn(21)):
            assert isinstance(result_ref, slay.core.reference.Reference)
            assert result_ref.get() == 42


    def test_run_preserves_tuple_argument(self, local_backend):
        """A tuple argument reaches the function as a tuple"""
        fn = slay.put(lambda t: type(t).__name__)
        assert slay.run(fn, (1, 2)).get() == "tuple"


class RecordingBackend:
    """Backend stand-in that records what run() and apply() pass to it"""

    name = "recording"
    max_execution_time = None

    def __init__(self):
        from slay.backends.local import LocalBackend

        self.calls = []
        self._local = LocalBackend()

    def execute(self, func_ref, args, kwargs):
        self.calls.append((args, kwargs))
        return self._local.execute(func_ref, args, kwargs)

    def execute_many(self, func_ref, args_list):
        return [self.execute(func_ref, args, kwargs) for args, kwargs in args_list]


class TestReferenceArguments:
    """A top-level Reference argument passes through and is resolved remotely"""

    @pytest.fixture
    def recorder(self, monkeypatch):
        backend = RecordingBackend()
        monkeypatch.setattr(slay.api, "get_backend", lambda: backend)
        return backend

    def test_function_receives_value_of_reference(self, recorder):
        """The function gets the value behind a Reference argument"""
        fn = slay.put(lambda x: type(x).__name__)

        assert fn(slay.put(5)).get() == "int"

    def test_positional_reference_is_not_wrapped(self, recorder):
        """run() hands a Reference argument to the backend unchanged"""
        five = slay.put(5)
        slay.run(slay.put(lambda x: x), five)

        args, _ = recorder.calls[-1]
        assert args[0] is five

    def test_keyword_reference_is_not_wrapped(self, recorder):
        """run() hands a Reference keyword argument to the backend unchanged"""
        three = slay.put(3)
        result = slay.run(slay.put(lambda x, factor: x * factor), 2, factor=three)

        _, kwargs = recorder.calls[-1]
        assert kwargs["factor"] is three
        assert result.get() == 6

    def test_keyword_values_become_references(self, recorder):
        """Plain keyword values reach the backend as a mapping of references"""
        slay.run(slay.put(lambda x=0: x), x=4)

        _, kwargs = recorder.calls[-1]
        assert set(kwargs) == {"x"}
        assert isinstance(kwargs["x"], slay.core.reference.Reference)
        assert kwargs["x"].get() == 4

    def test_apply_passes_reference_items(self, recorder):
        """apply() hands Reference items to the backend unchanged"""
        one = slay.put(1)
        refs = slay.apply(slay.put(lambda x: x * 2), [one, 2])

        assert recorder.calls[0][0][0] is one
        assert slay.get_many(refs) == [2, 4]

    def test_chained_results(self, recorder):
        """A result Reference feeds the next call"""
        double = slay.put(lambda x: x * 2)

        assert double(double(slay.put(3))).get() == 12

    def test_nested_reference_is_not_resolved(self, recorder):
        """A Reference inside a list reaches the function as a Reference"""
        fn = slay.put(lambda xs: type(xs[0]).__name__)

        assert fn([slay.put(5)]).get() == "Reference"


class TestApply:
    """Tests for apply() - parallel function application"""

    def test_apply_simple(self, local_backend):
        """Can apply function to list of items"""
        fn = slay.put(lambda x: x * 2)
        result_refs = slay.apply(fn, [1, 2, 3, 4, 5])
        results = slay.get_many(result_refs)
        
        assert results == [2, 4, 6, 8, 10]

    def test_apply_returns_references(self, local_backend):
        """apply() returns list of References"""
        fn = slay.put(lambda x: x + 1)
        result_refs = slay.apply(fn, [1, 2, 3])
        
        assert isinstance(result_refs, list)
        assert all(isinstance(ref, slay.core.reference.Reference) for ref in result_refs)

    def test_apply_empty_list(self, local_backend):
        """apply() with empty list returns empty list"""
        fn = slay.put(lambda x: x)
        result_refs = slay.apply(fn, [])
        
        assert result_refs == []

    def test_apply_single_item(self, local_backend):
        """apply() works with single item"""
        fn = slay.put(lambda x: x * 2)
        result_refs = slay.apply(fn, [42])
        
        assert len(result_refs) == 1
        assert result_refs[0].get() == 84

    def test_apply_preserves_order(self, local_backend):
        """apply() preserves order of inputs"""
        fn = slay.put(lambda x: x ** 2)
        result_refs = slay.apply(fn, [5, 3, 4, 1, 2])
        results = slay.get_many(result_refs)
        
        assert results == [25, 9, 16, 1, 4]

    def test_apply_complex_function(self, local_backend):
        """apply() works with complex functions"""
        def extract_name(person):
            return person["name"].upper()
        
        fn = slay.put(extract_name)
        people = [
            {"name": "alice", "age": 30},
            {"name": "bob", "age": 25},
            {"name": "charlie", "age": 35},
        ]
        result_refs = slay.apply(fn, people)
        results = slay.get_many(result_refs)
        
        assert results == ["ALICE", "BOB", "CHARLIE"]


class TestGetMany:
    """Tests for get_many() - parallel fetching"""

    def test_get_many_simple(self, local_backend):
        """Can fetch multiple references at once"""
        refs = [slay.put(i) for i in [1, 2, 3]]
        results = slay.get_many(refs)
        
        assert results == [1, 2, 3]

    def test_get_many_empty_list(self, local_backend):
        """get_many([]) returns empty list"""
        results = slay.get_many([])
        assert results == []

    def test_get_many_preserves_order(self, local_backend):
        """get_many() preserves order of inputs"""
        refs = [slay.put(x) for x in [5, 3, 4, 1, 2]]
        results = slay.get_many(refs)
        
        assert results == [5, 3, 4, 1, 2]

    def test_get_many_with_complex_data(self, local_backend):
        """get_many() works with complex data"""
        data_list = [
            {"name": "alice", "age": 30},
            {"name": "bob", "age": 25},
            {"name": "charlie", "age": 35},
        ]
        refs = [slay.put(data) for data in data_list]
        results = slay.get_many(refs)
        
        assert results == data_list

    def test_get_many_uses_cache(self, local_backend):
        """get_many() should use cached data when available"""
        ref = slay.put("data")
        
        # First access caches it
        ref.get()
        
        # get_many should use cache
        results = slay.get_many([ref])
        assert results == ["data"]


class TestComposition:
    """Tests for composing operations together"""

    def test_put_and_call(self, local_backend):
        """Complete workflow: put function, call it, get the result"""
        fn = slay.put(lambda x: x * 2)

        assert fn(21).get() == 42

    def test_put_run_and_get(self, local_backend):
        """Workflow with run(): put, run, get"""
        fn = slay.put(lambda x: x * 2)
        result_ref = slay.run(fn, 21)  # run() returns Reference
        result = result_ref.get()  # Must deserialize manually
        
        assert result == 42

    def test_apply_and_get_many(self, local_backend):
        """Complete workflow: apply function, get many results"""
        fn = slay.put(lambda x: x ** 2)
        result_refs = slay.apply(fn, [1, 2, 3, 4, 5])
        results = slay.get_many(result_refs)
        
        assert results == [1, 4, 9, 16, 25]

    def test_nested_execution_with_call_syntax(self, local_backend):
        """Can nest function executions using call syntax"""
        add_one = slay.put(lambda x: x + 1)
        double = slay.put(lambda x: x * 2)

        result1 = add_one(5)  # Reference to 6
        result2 = double(result1)  # result1 passes through as a reference

        assert result2.get() == 12  # (5+1)*2

    def test_nested_execution_with_run_syntax(self, local_backend):
        """Can nest function executions using run() syntax"""
        add_one = slay.put(lambda x: x + 1)
        double = slay.put(lambda x: x * 2)
        
        # run() returns references
        result1_ref = slay.run(add_one, 5)
        result1 = result1_ref.get()  # 6
        result2_ref = slay.run(double, result1)
        result2 = result2_ref.get()  # 12
        
        assert result2 == 12


class TestBackendSelection:
    """Tests for backend selection"""

    def test_set_backend_local(self):
        """Can explicitly set local backend"""
        slay.set_backend("local")
        assert slay.get_backend().name == "local"

    def test_backend_auto_detects_local(self, local_backend):
        """Backend auto-detects local when AWS unavailable"""
        # local_backend fixture already sets this
        assert slay.get_backend().name == "local"
