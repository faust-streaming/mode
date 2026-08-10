"""Regression tests for the thread-safety fixes.

These all guard defects that free-threaded (:pep:`703`) builds made
reachable in practice.  They are written to fail deterministically on a
GIL-enabled interpreter too, so the whole matrix protects them rather than
just the ``3.14t`` leg.

See `docs/free-threading.md` for the measurements behind each one, and
`tests/freethreading/stress.py` for the heavier probabilistic reproducers.
"""

import inspect
import pickle
import sys
import threading
import time
from collections import OrderedDict
from contextlib import nullcontext
from types import ModuleType

import pytest

import mode
from mode.proxy import ServiceProxy
from mode.signals import Signal
from mode.utils.collections import FREE_THREADED, FastUserDict, LRUCache
from mode.utils.objects import cached_property

#: Upper bound for any one concurrency test below.  Generous: these
#: finish in well under a second when they are healthy, and the point of
#: the bound is only to keep a wedged thread from waiting forever.
RACE_TIMEOUT = 60.0


def race(work, nthreads=8, timeout=RACE_TIMEOUT):
    """Run ``work(i)`` in `nthreads` threads released together.

    Returns the exceptions the workers raised, for the caller to assert
    on.  A worker still running after `timeout` fails the test here.

    Every wait is bounded on purpose.  The defects these tests cover
    show up as a thread that stops making progress, and an unbounded
    `threading.Barrier.wait` or `threading.Thread.join` turns that into
    a CI job that reports nothing until it hits its own time limit --
    six hours, in the case that prompted this helper.  Bounded, the same
    defect fails in a minute and names the test it happened in.
    """
    barrier = threading.Barrier(nthreads)
    errors = []

    def target(i):
        try:
            barrier.wait(timeout=timeout)
            work(i)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=target, args=(i,), daemon=True)
        for i in range(nthreads)
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    still_running = sum(1 for t in threads if t.is_alive())
    assert not still_running, (
        f"{still_running}/{nthreads} threads still running after {timeout}s"
    )
    return errors


class test_cached_property_is_computed_once:
    def _race_on(self, obj, nthreads=8):
        seen = []
        lock = threading.Lock()

        def work(i):
            value = obj.val
            with lock:
                seen.append(value)

        assert not race(work, nthreads)
        return seen

    def test_concurrent_miss_computes_once(self):
        # The getter sleeps, which releases the GIL, so without the lock in
        # `cached_property.__get__` every thread would enter it and store a
        # different object.  This fails on GIL builds too, by design.
        calls = []
        calls_lock = threading.Lock()

        class X:
            @cached_property
            def val(self):
                with calls_lock:
                    calls.append(1)
                time.sleep(0.05)
                return object()

        seen = self._race_on(X())

        assert len(calls) == 1
        assert len({id(v) for v in seen}) == 1

    def test_service_proxy_service_is_a_singleton(self):
        # ServiceProxy documents @cached_property _service as the way to
        # build the proxied service, so a duplicate there means start() and
        # stop() can act on different Service instances.
        built = []
        built_lock = threading.Lock()

        class MyProxy(ServiceProxy):
            @cached_property
            def _service(self):
                service = mode.Service()
                with built_lock:
                    built.append(service)
                time.sleep(0.05)
                return service

        proxy = MyProxy()
        seen = []
        seen_lock = threading.Lock()

        def work(i):
            # Resolve outside the lock -- holding it here would serialise
            # the very access this test is trying to race.
            service = proxy._service
            with seen_lock:
                seen.append(service)

        assert not race(work)

        assert len(built) == 1
        assert len({id(s) for s in seen}) == 1

    def test_cached_value_is_still_reused(self):
        calls = []

        class X:
            @cached_property
            def val(self):
                calls.append(1)
                return object()

        x = X()
        assert x.val is x.val
        assert len(calls) == 1


class test_LRUCache_thread_safety:
    def test_backed_by_ordered_dict(self):
        # OrderedDict, not plain dict: evicting the oldest entry is the hot
        # path and OrderedDict does it in O(1), where dict has to scan past
        # every slot vacated since its last resize.  The concurrency
        # hazard that comes with it is handled by making the mutex
        # mandatory on free-threaded builds, not by changing container.
        assert type(LRUCache().data) is OrderedDict

    def test_thread_safety_defaults_to_free_threaded(self):
        assert LRUCache().thread_safety is FREE_THREADED

    def test_thread_safety_can_be_requested(self):
        assert LRUCache(thread_safety=True).thread_safety is True

    def test_thread_safety_cannot_be_disabled_when_free_threaded(self):
        # An unguarded OrderedDict is memory-unsafe here, not merely racy,
        # so this is refused rather than honoured.
        if FREE_THREADED:
            with pytest.raises(ValueError, match="free-threaded"):
                LRUCache(thread_safety=False)
        else:
            assert LRUCache(thread_safety=False).thread_safety is False

    # (Pure eviction/popitem/ordering semantics live in
    # tests/functional/utils/test_collections.py::test_LRUCache_ordering;
    # this class only covers what involves the mutex or threads.)

    def test_eviction_does_not_iterate_the_data(self):
        # Eviction must be `popitem(last=False)` -- one call -- and not
        # the historical `pop(next(iter(data)))`.  An *unlocked* cache on
        # a GIL build (the historical default there) races the latter's
        # iter/next/pop gaps: a switch between `iter` and `next` while
        # another thread inserts raises "OrderedDict mutated during
        # iteration", and two threads resolving the same oldest key make
        # the loser's `pop` raise KeyError.  A stress test cannot tell
        # the two forms apart on a locked cache, so assert the mechanism
        # itself, by recording `__iter__` calls on the backing dict.
        # Recording, not raising: whether *other* operations -- the
        # views, `dict()` -- route through `__iter__` varies between
        # OrderedDict implementations (C, pure-Python, PyPy), and only
        # iteration *during the fill* is the defect.  `popitem` itself
        # iterates on none of them.
        iterations = []

        class RecordingData(OrderedDict):
            def __iter__(self):
                iterations.append(True)
                return super().__iter__()

        c = LRUCache(limit=3)
        c.data = RecordingData()
        for i in range(10):
            c[i] = i
        assert not iterations, "eviction iterated the backing dict"
        assert list(c.data.keys()) == [7, 8, 9]

    def test_iteration_does_not_hold_the_lock_across_yields(self):
        # A half-consumed iterator must not keep the mutex held: the lock
        # is reentrant, so only a *different* thread shows the problem.
        # Previously the writer below blocked until the abandoned
        # generator was collected.
        c = LRUCache(limit=100, thread_safety=True)
        c.update({"a": 1, "b": 2, "c": 3})
        it = iter(c.keys())
        next(it)  # deliberately left half-consumed

        done = threading.Event()

        def writer():
            c["d"] = 4
            done.set()

        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(timeout=10.0)

        assert done.is_set(), "writer blocked on a half-consumed iterator"
        assert c["d"] == 4

    def test_concurrent_mutation_and_iteration(self):
        # thread_safety=True explicitly, NOT the default.  On a
        # free-threaded build they are the same configuration -- the
        # default resolves to True there, which has its own test above --
        # so this still hammers the exact setup that used to segfault the
        # interpreter.  On a GIL build the default is *deliberately*
        # unlocked, and racing that asserts nothing the class promises:
        # the eviction in `__setitem__` is a check-then-act that a GIL
        # switch can split, which surfaced in CI as a one-in-many-runs
        # "OrderedDict mutated during iteration".
        c = LRUCache(limit=50, thread_safety=True)

        def work(i):
            for n in range(200):
                c[f"{i}-{n}"] = n
                list(c.keys())
                list(c.items())
                list(c.values())

        assert not race(work)

    def test_concurrent_mapping_surface(self):
        # The test above only drives the methods LRUCache defines itself.
        # Every other mapping operation used to be inherited straight from
        # FastUserDict, reaching self.data with the mutex released.
        # thread_safety=True for the same reason as above: the locked
        # configuration is the one that promises this workload is safe.
        c = LRUCache(limit=50, thread_safety=True)

        def work(i):
            for n in range(200):
                key = f"{i}-{n}"
                c[key] = n
                len(c)
                key in c  # noqa: B015
                repr(c)
                c.copy()
                c.get(key)
                c.setdefault(f"sd-{i}", n)
                c.pop(key, None)
                if not n % 50:
                    c.clear()

        assert not race(work)


class test_LRUCache_takes_the_mutex:
    """Every operation reaching ``data`` must go through ``_mutex``.

    `LRUCache` inherits most of its mapping surface from `FastUserDict`,
    whose implementations use ``self.data`` directly.  An override that
    goes missing is invisible to a stress test -- it just makes the race
    window smaller -- so assert lock entry directly instead.
    """

    class TrackingMutex:
        def __init__(self) -> None:
            self.enters = 0

        def __enter__(self) -> None:
            self.enters += 1

        def __exit__(self, *exc_info: object) -> None:
            pass

    def assert_takes_mutex(self, operation):
        cache = LRUCache(limit=10, thread_safety=True)
        # Populate first: the tracking mutex is installed after, so the
        # count below only reflects the operation under test.
        cache.update({"a": 1, "b": 2})
        mutex = self.TrackingMutex()
        cache._mutex = mutex

        operation(cache)

        assert mutex.enters, "operation reached .data without the mutex"

    @pytest.mark.parametrize(
        "name,operation",
        [
            ("__setitem__", lambda c: c.__setitem__("c", 3)),
            ("__getitem__", lambda c: c["a"]),
            ("__delitem__", lambda c: c.__delitem__("a")),
            ("__len__", len),
            ("__contains__", lambda c: "a" in c),
            ("__repr__", repr),
            ("__iter__", lambda c: list(iter(c))),
            ("keys", lambda c: list(c.keys())),
            ("values", lambda c: list(c.values())),
            ("items", lambda c: list(c.items())),
            ("copy", lambda c: c.copy()),
            ("clear", lambda c: c.clear()),
            ("update", lambda c: c.update({"c": 3})),
            ("popitem", lambda c: c.popitem()),
            ("pop", lambda c: c.pop("a")),
            ("pop-default", lambda c: c.pop("missing", None)),
            ("setdefault-hit", lambda c: c.setdefault("a", 0)),
            ("setdefault-miss", lambda c: c.setdefault("z", 0)),
            ("get-hit", lambda c: c.get("a")),
            ("get-miss", lambda c: c.get("missing")),
            ("incr", lambda c: c.incr("a")),
        ],
    )
    def test_operation_takes_mutex(self, name, operation):
        self.assert_takes_mutex(operation)

    def test_every_FastUserDict_method_is_overridden(self):
        # The override list above is hand-maintained, and so is this
        # test's parametrization -- neither notices a method *added* to
        # `FastUserDict` later, which would reach `self.data` with the
        # mutex released (the NOTE in LRUCache admits as much).  Enforce
        # the completeness invariant reflectively: every function defined
        # on `FastUserDict` must be shadowed by `LRUCache` itself.
        # (`fromkeys` is exempt: a classmethod that only touches data
        # through the locked `update`.)
        missing = [
            name
            for name, member in vars(FastUserDict).items()
            if inspect.isfunction(member) and name not in vars(LRUCache)
        ]
        assert not missing, (
            f"FastUserDict methods that LRUCache does not override "
            f"(they would touch self.data without the mutex): {missing}"
        )


class test_LRUCache_mapping_semantics:
    """The mutex overrides must not change what the methods do."""

    def test_pop_returns_and_removes(self):
        c = LRUCache()
        c.update({"a": 1, "b": 2})
        assert c.pop("a") == 1
        assert "a" not in c
        assert len(c) == 1

    def test_pop_missing_raises_KeyError(self):
        with pytest.raises(KeyError):
            LRUCache().pop("a")

    def test_pop_missing_returns_default(self):
        assert LRUCache().pop("a", "default") == "default"
        # None has to stay usable as a default, so the "no default given"
        # sentinel cannot be None.
        assert LRUCache().pop("a", None) is None

    def test_setdefault_stores_and_returns(self):
        c = LRUCache()
        assert c.setdefault("a", 1) == 1
        assert c.setdefault("a", 2) == 1
        assert c["a"] == 1

    def test_get(self):
        c = LRUCache()
        c["a"] = 1
        assert c.get("a") == 1
        assert c.get("b") is None
        assert c.get("b", "default") == "default"

    def test_len_contains_and_repr(self):
        c = LRUCache()
        c.update({"a": 1})
        assert len(c) == 1
        assert "a" in c
        assert "b" not in c
        assert repr(c) == repr(c.data)

    def test_copy_is_a_plain_dict_snapshot(self):
        c = LRUCache()
        c.update({"a": 1})
        copy = c.copy()
        assert copy == {"a": 1}
        assert type(copy) is dict
        c["b"] = 2
        assert copy == {"a": 1}

    def test_del_and_clear(self):
        c = LRUCache()
        c.update({"a": 1, "b": 2})
        del c["a"]
        assert list(c.keys()) == ["b"]
        c.clear()
        assert not len(c)
        with pytest.raises(KeyError):
            del c["a"]

    def test_pop_does_not_reinsert_the_key(self):
        # __getitem__ pops and reinserts to mark the key most recently
        # used; pop() must not leave it behind while doing that.
        c = LRUCache(limit=3)
        c.update({"a": 1, "b": 2, "c": 3})
        assert c.pop("a") == 1
        assert list(c.keys()) == ["b", "c"]


class test_LRUCache_pickle:
    def test_roundtrip_keeps_data_and_limit(self):
        c = LRUCache(limit=3)
        c.update({"a": 1, "b": 2})
        restored = pickle.loads(pickle.dumps(c))
        assert restored.limit == 3
        assert list(restored.items()) == [("a", 1), ("b", 2)]
        assert restored.thread_safety is c.thread_safety

    def test_restored_cache_is_usable(self):
        restored = pickle.loads(pickle.dumps(LRUCache(limit=2)))
        restored["a"] = 1
        restored["b"] = 2
        restored["c"] = 3
        assert list(restored.keys()) == ["b", "c"]

    def test_unsafe_pickle_is_upgraded_when_free_threaded(self, monkeypatch):
        # Unpickling is another construction path, so it has to honour the
        # invariant __init__ enforces.  A pickle written on a GIL build --
        # where thread_safety=False is both legal and the default -- used
        # to restore a free-threaded cache with a nullcontext for a mutex.
        monkeypatch.setattr("mode.utils.collections.FREE_THREADED", False)
        payload = pickle.dumps(LRUCache(thread_safety=False))

        monkeypatch.setattr("mode.utils.collections.FREE_THREADED", True)
        restored = pickle.loads(payload)

        assert restored.thread_safety is True
        assert not isinstance(restored._mutex, nullcontext)

    def test_unsafe_pickle_is_left_alone_with_the_gil(self, monkeypatch):
        monkeypatch.setattr("mode.utils.collections.FREE_THREADED", False)
        restored = pickle.loads(pickle.dumps(LRUCache(thread_safety=False)))

        assert restored.thread_safety is False
        assert isinstance(restored._mutex, nullcontext)

    def test_setstate_preserves_true_thread_safety(self, monkeypatch):
        monkeypatch.setattr("mode.utils.collections.FREE_THREADED", True)
        restored = pickle.loads(pickle.dumps(LRUCache(thread_safety=True)))

        assert restored.thread_safety is True
        assert not isinstance(restored._mutex, nullcontext)


class test_Signal_receiver_iteration:
    def test_get_live_receivers_tolerates_mutation(self):
        # Directly simulate a connect() landing while the receiver set is
        # being walked.  Before the snapshot this raised
        # "Set changed size during iteration".
        signal = Signal()

        async def handler(*args, **kwargs): ...

        async def late_handler(*args, **kwargs): ...

        signal.connect(handler)
        receivers = signal._receivers
        original_is_alive = signal._is_alive

        def mutating_is_alive(ref):
            receivers.add(late_handler)
            return original_is_alive(ref)

        signal._is_alive = mutating_is_alive

        live, _dead = signal._get_live_receivers(receivers)
        assert live

    def test_iter_receivers_while_connecting(self):
        class Owner:
            sig = Signal()

        owner = Owner()
        signal = Owner.sig

        def work(i):
            for _n in range(200):

                async def handler(*args, **kwargs): ...

                if i % 2:
                    signal.connect(handler)
                    signal.disconnect(handler)
                else:
                    list(signal.iter_receivers(owner))

        assert not race(work)
        # Every connect above was paired with a disconnect, so the set has
        # to be empty.  Without this the test proved much less than it
        # looked like it did: disconnect() was a no-op for strong
        # receivers, so the "mutation" being raced was only ever growth.
        assert not signal._receivers


class test_mode_lazy_imports:
    def test_module_is_not_replaced_in_sys_modules(self):
        # The old Werkzeug-style trick swapped sys.modules["mode"] for a
        # ModuleType *subclass* at the end of mode/__init__.py.  That swap
        # was the race: a thread importing mode concurrently could be
        # handed the original pre-swap module, which had no __getattr__.
        # A PEP 562 module __getattr__ needs no swap at all.
        assert type(sys.modules["mode"]) is ModuleType

    def test_module_keeps_its_spec(self):
        # The replacement module carried no __spec__, which denied the
        # import machinery the _initializing flag it uses to make a second
        # importing thread wait.
        assert mode.__spec__ is not None
        assert mode.__spec__.name == "mode"

    def test_lazy_names_resolve(self):
        from mode.services import Service

        assert mode.Service is Service

    def test_resolving_one_name_binds_its_siblings(self):
        assert mode.task is not None
        assert "timer" in vars(mode)

    def test_unknown_attribute_raises_AttributeError(self):
        with pytest.raises(AttributeError) as excinfo:
            mode.NoSuchThing  # noqa: B018
        assert "NoSuchThing" in str(excinfo.value)

    def test_dir_lists_the_lazy_names(self):
        listed = dir(mode)
        for name in mode.__all__:
            assert name in listed

    def test_all_matches_the_lazy_export_table(self):
        # `__all__` is the literal copy ruff and mypy read; `all_by_module`
        # is what `__getattr__` actually resolves.  A name added to one
        # and not the other would silently vanish from `import *` or
        # raise AttributeError -- so the two may not drift.
        assert set(mode.__all__) == set(mode.object_origins)

    def test_dir_advertises_only_real_names(self):
        # The old hand-written __dir__ promised VERSION/version_info,
        # which no version of this module ever defined.
        for name in dir(mode):
            assert hasattr(mode, name), name
