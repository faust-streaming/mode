"""Regression tests for the thread-safety fixes.

These all guard defects that free-threaded (:pep:`703`) builds made
reachable in practice.  They are written to fail deterministically on a
GIL-enabled interpreter too, so the whole matrix protects them rather than
just the ``3.14t`` leg.

See `docs/free-threading.md` for the measurements behind each one, and
`tests/freethreading/stress.py` for the heavier probabilistic reproducers.
"""

import sys
import threading
import time
from collections import OrderedDict
from types import ModuleType

import pytest

import mode
from mode.proxy import ServiceProxy
from mode.signals import Signal
from mode.utils.collections import FREE_THREADED, LRUCache
from mode.utils.objects import cached_property


class test_cached_property_is_computed_once:
    def _race_on(self, obj, nthreads=8):
        barrier = threading.Barrier(nthreads)
        seen = []
        lock = threading.Lock()

        def work():
            barrier.wait()
            value = obj.val
            with lock:
                seen.append(value)

        threads = [threading.Thread(target=work) for _ in range(nthreads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
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
        barrier = threading.Barrier(8)
        seen = []
        seen_lock = threading.Lock()

        def work():
            barrier.wait()
            # Resolve outside the lock -- holding it here would serialise
            # the very access this test is trying to race.
            service = proxy._service
            with seen_lock:
                seen.append(service)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

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

    def test_popitem_last_is_lifo(self):
        c = LRUCache()
        c.update({"a": 1, "b": 2, "c": 3})
        assert c.popitem() == ("c", 3)
        assert c.popitem(last=True) == ("b", 2)

    def test_popitem_first_is_fifo(self):
        c = LRUCache()
        c.update({"a": 1, "b": 2, "c": 3})
        assert c.popitem(last=False) == ("a", 1)
        assert c.popitem(last=False) == ("b", 2)

    def test_popitem_empty_raises_KeyError(self):
        with pytest.raises(KeyError):
            LRUCache().popitem()
        with pytest.raises(KeyError):
            LRUCache().popitem(last=False)

    def test_limit_still_evicts_oldest(self):
        c = LRUCache(limit=3)
        for i in range(10):
            c[i] = i
        assert list(c.keys()) == [7, 8, 9]

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
        # Deliberately the *default* configuration -- which on a
        # free-threaded build now means the mutex is on.  This is the
        # workload that used to segfault the interpreter.
        c = LRUCache(limit=50)
        barrier = threading.Barrier(8)
        errors = []

        def work(i):
            barrier.wait()
            try:
                for n in range(200):
                    c[f"{i}-{n}"] = n
                    list(c.keys())
                    list(c.items())
                    list(c.values())
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


class test_Signal_receiver_iteration:
    def test_get_live_receivers_tolerates_mutation(self):
        # Directly simulate a connect() landing while the receiver set is
        # being walked.  Before the snapshot this raised
        # "Set changed size during iteration".
        signal = Signal()

        async def handler(*args, **kwargs): ...

        for _ in range(4):
            signal.connect(handler)
        receivers = signal._receivers
        original_is_alive = signal._is_alive

        def mutating_is_alive(ref):
            receivers.add(lambda: handler)
            return original_is_alive(ref)

        signal._is_alive = mutating_is_alive

        live, _dead = signal._get_live_receivers(receivers)
        assert live

    def test_iter_receivers_while_connecting(self):
        class Owner:
            sig = Signal()

        owner = Owner()
        signal = Owner.sig
        barrier = threading.Barrier(8)
        errors = []

        def work(i):
            barrier.wait()
            try:
                for _n in range(200):

                    async def handler(*args, **kwargs): ...

                    if i % 2:
                        signal.connect(handler)
                        signal.disconnect(handler)
                    else:
                        list(signal.iter_receivers(owner))
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


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
