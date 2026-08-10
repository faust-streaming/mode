"""Free-threading (PEP 703) stress reproducers for mode.

Every check here should now report ``ok``.  Each one reproduced a real
defect before the fix it guards, and they are kept because they are
probabilistic and heavy -- they hammer each surface with 16 threads over
many trials, which is how the ``tuple(r)``-is-not-atomic problem in
`mode.signals` was caught after the first attempt at that fix passed the
cheaper tests.

The deterministic versions live in
`tests/functional/test_thread_safety.py` and run in CI.  This file is
deliberately NOT under the ``testpaths`` configured in ``pyproject.toml``:
before the fixes some of these checks segfaulted the interpreter, and a
regression here should not take the whole test run down with it.

```sh
uv python install 3.14t
uv venv --python 3.14t .venv-ft
VIRTUAL_ENV=.venv-ft uv pip install -e . -r requirements-tests.txt
.venv-ft/bin/python tests/freethreading/stress.py
```

Run it under a GIL-enabled interpreter of the same version too -- the
fixes are meant to hold on both.

See `docs/free-threading.md` for the measurements and the analysis.
"""

import sys
import threading
import time
import traceback

NTHREADS = 16

#: Upper bound on any single race() call.  A wedge-class regression -- the
#: kind this harness exists to catch -- must fail the check that hit it,
#: not hang a manual run: the same bounded-wait policy as the CI-facing
#: `race` helper in tests/functional/test_thread_safety.py.
RACE_TIMEOUT = 60.0


def race(target, nthreads=NTHREADS, timeout=RACE_TIMEOUT):
    """Run ``target(i)`` in ``nthreads`` threads released by a barrier.

    Returns the list of tracebacks raised by the threads (empty if none).
    A thread still running after `timeout` is reported as an error.
    """
    barrier = threading.Barrier(nthreads)
    errors = []

    def wrapper(i):
        try:
            barrier.wait(timeout=timeout)
            target(i)
        except BaseException:
            errors.append(traceback.format_exc())

    threads = [
        threading.Thread(target=wrapper, args=(i,), daemon=True)
        for i in range(nthreads)
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))
    wedged = sum(1 for t in threads if t.is_alive())
    if wedged:
        errors.append(
            f"WEDGED: {wedged}/{nthreads} threads still running "
            f"after {timeout}s"
        )
    return errors


def report(name, errors, note=""):
    if errors:
        last_line = errors[0].strip().splitlines()[-1]
        print(f"[FAIL] {name}: {len(errors)} threads -> {last_line}")
    else:
        print(f"[ok  ] {name} {note}".rstrip())


def report_trials(name, failed, summary):
    """The `[FAIL]/[ok  ]` line for checks that count failing trials."""
    print(f"[{'FAIL' if failed else 'ok  '}] {name}: {summary}")


# --------------------------------------------------------------------------
# Defect 1 (fixed): LRUCache is backed by OrderedDict, whose C linked list
# concurrent mutation can corrupt badly enough to segfault a free-threaded
# interpreter -- and thread_safety defaulted to False.  The container is
# unchanged (dict cannot evict the oldest entry in O(1)); instead the mutex
# is now mandatory on free-threaded builds, so the default config is safe
# and thread_safety=False is refused there.
# --------------------------------------------------------------------------
def _lru_iteration_workload(i, cache):
    for n in range(100):
        cache[f"{i}-{n}"] = n
        list(cache.keys())
        list(cache.items())


def _lru_mapping_workload(i, cache):
    for n in range(100):
        key = f"{i}-{n}"
        cache[key] = n
        len(cache)
        key in cache  # noqa: B015
        repr(cache)
        cache.copy()
        cache.get(key)
        cache.setdefault(f"sd-{i}", n)
        cache.pop(key, None)
        try:
            del cache[f"{i}-{n - 1}"]
        except KeyError:
            pass
        if not n % 25:
            cache.clear()


def _lru_trials(name, workload, trials, **kwargs):
    from mode.utils.collections import LRUCache

    bad = 0
    for _ in range(trials):
        cache = LRUCache(limit=50, **kwargs)
        if race(lambda i, cache=cache: workload(i, cache)):
            bad += 1
    report_trials(name, bad, f"{bad}/{trials} trials raised")


def check_lru_default(trials=60):
    print("  (this configuration segfaulted before the fix)", flush=True)
    _lru_trials("LRUCache(default)", _lru_iteration_workload, trials)


def check_lru_thread_safe(trials=20):
    _lru_trials(
        "LRUCache(thread_safety=True)",
        _lru_iteration_workload,
        trials,
        thread_safety=True,
    )


def check_lru_mapping_surface(trials=40):
    # The iteration workload only drives the methods LRUCache defines
    # itself.  Every other mapping operation used to be inherited from
    # FastUserDict, which reaches self.data with the mutex released -- so
    # `del`, `clear`, `copy`, `len`, `in` and `repr` had the same
    # unguarded OrderedDict access that the segfault came from.  Race
    # them against writers.
    _lru_trials("LRUCache(mapping surface)", _lru_mapping_workload, trials)


# --------------------------------------------------------------------------
# Defect 2 (fixed): cached_property.__get__ was a non-atomic check-then-act
# on obj.__dict__, so racing threads each computed and handed out a distinct
# object.  ServiceProxy documents @cached_property as the way to build the
# proxied service, so the duplicate was a real singleton violation.  The
# miss path is double-checked under a lock now.
# --------------------------------------------------------------------------
def check_cached_property(trials=300):
    from mode.utils.objects import cached_property

    computes = [0]
    bad = 0
    for _ in range(trials):

        class X:
            @cached_property
            def val(self):
                computes[0] += 1
                return object()

        x = X()
        seen = []
        lock = threading.Lock()

        def work(i, x=x, seen=seen, lock=lock):
            value = x.val
            with lock:
                seen.append(value)

        race(work)
        if len({id(v) for v in seen}) != 1:
            bad += 1
    report_trials(
        "cached_property",
        bad,
        f"{bad}/{trials} trials returned >1 distinct object "
        f"({computes[0]} computes for {trials} properties)",
    )


def check_service_proxy(trials=200):
    from mode import Service
    from mode.proxy import ServiceProxy
    from mode.utils.objects import cached_property

    bad = 0
    for _ in range(trials):
        built = []
        build_lock = threading.Lock()

        class MyProxy(ServiceProxy):
            @cached_property
            def _service(self, built=built, build_lock=build_lock):
                service = Service()
                with build_lock:
                    built.append(service)
                return service

        proxy = MyProxy()
        seen = []
        seen_lock = threading.Lock()

        def work(i, proxy=proxy, seen=seen, seen_lock=seen_lock):
            service = proxy._service
            with seen_lock:
                seen.append(service)

        race(work)
        if len({id(s) for s in seen}) != 1 or len(built) != 1:
            bad += 1
    report_trials(
        "ServiceProxy._service",
        bad,
        f"{bad}/{trials} trials built/returned >1 Service instance",
    )


# --------------------------------------------------------------------------
# Defect 4 (fixed): Signal iterated its receiver set while connect/disconnect
# mutated it.  Pre-existing -- this failed on GIL builds too.  It snapshots
# with list() now (NOT tuple(), which does not lock the source set).
#
# The disconnect half of that race only became real once strong receivers
# were stored as the handler itself: they used to be `lambda: fun`, and
# disconnect built a second lambda that could never compare equal, so the
# receiver set grew monotonically and was never actually mutated by
# disconnect().  The leftover count below is asserted, not just the
# absence of exceptions.
#
# Storing them bare also keeps hashing and comparison in the interpreter.
# A wrapper defining __eq__/__hash__ in Python makes set.add/set.discard
# re-enter the interpreter mid-operation, releasing the GIL while the set
# is being walked; that wedges this loop outright on PyPy.
# --------------------------------------------------------------------------
def check_signal(trials=30):
    from mode.signals import Signal

    bad = 0
    leaked = 0
    for _ in range(trials):

        class Owner:
            sig = Signal()

        owner = Owner()
        sig = Owner.sig

        def work(i, sig=sig, owner=owner):
            for _n in range(100):

                async def handler(*args, **kwargs):
                    pass

                if i % 2:
                    sig.connect(handler)
                    sig.disconnect(handler)
                else:
                    list(sig.iter_receivers(owner))

        if race(work):
            bad += 1
        if sig._receivers:
            leaked += 1
    report_trials(
        "Signal iter_receivers",
        bad or leaked,
        f"{bad}/{trials} trials raised, "
        f"{leaked}/{trials} left receivers connected",
    )


# --------------------------------------------------------------------------
# Surfaces verified SAFE under the same stress -- kept so regressions show up.
# --------------------------------------------------------------------------
def check_service_subclass_creation():
    from mode import Service

    made = []
    lock = threading.Lock()

    def work(i):
        local = []
        for n in range(50):

            async def a_task(self):
                pass

            namespace = {
                "__module__": f"stressmod{i}",
                "__qualname__": f"S{i}_{n}",
                "t": Service.task(a_task),
            }
            local.append(type(f"S{i}_{n}", (Service,), namespace))
        with lock:
            made.extend(local)

    errors = race(work)
    if not errors:
        for cls in made:
            clsid = cls._get_class_id()
            if cls._tasks.get(clsid) != {"t"}:
                errors.append(f"{clsid} -> {cls._tasks.get(clsid)!r}")
    report(
        "Service subclass creation (cls._tasks)",
        errors,
        f"({len(made)} classes)",
    )


def check_get_event_loop():
    import asyncio

    from mode.utils.loops import get_event_loop

    seen = {}
    lock = threading.Lock()

    def work(i):
        loops = {get_event_loop() for _ in range(200)}
        assert len(loops) == 1, f"thread saw {len(loops)} loops"
        with lock:
            seen[threading.get_ident()] = loops.pop()

    errors = race(work)
    ids = [id(v) for v in seen.values()]
    if len(set(ids)) != len(ids):
        errors.append("event loop leaked across threads")
    for loop in seen.values():
        loop.close()
    asyncio.set_event_loop(None)
    report("get_event_loop() thread-local cache", errors)


def check_service_thread():
    import asyncio

    from mode.threads import ServiceThread

    class T(ServiceThread):
        pass

    def work(i):
        async def main():
            service = T()
            await service.start()
            await service.stop()

        asyncio.run(main())

    report("ServiceThread start/stop", race(work, nthreads=8))


def check_beacon():
    from mode.utils.trees import Node

    root = Node("root")
    for i in range(50):
        root.new(f"pre-{i}")

    def work(i):
        for n in range(200):
            if i % 2:
                child = root.new(f"{i}-{n}")
                root.discard(child.data)
            else:
                list(root.traverse())
                root.as_graph()

    report("Node.traverse while mutating", race(work))


def check_managed_user_dict():
    from mode.utils.collections import ManagedUserDict

    class D(ManagedUserDict):
        def __init__(self):
            self.data = {}

        def on_key_get(self, key): ...
        def on_key_set(self, key, value): ...
        def on_key_del(self, key): ...
        def on_clear(self): ...

    d = D()

    def work(i):
        for n in range(400):
            d[f"{i}-{n}"] = n
            d.get(f"{i}-{n}")
            del d[f"{i}-{n}"]

    errors = race(work)
    if not errors and len(d):
        errors.append(f"{len(d)} leftover keys")
    report("ManagedUserDict mutation", errors)


# --------------------------------------------------------------------------
# Defect 3 (fixed): mode/__init__.py swapped sys.modules["mode"] for a
# _module instance at the END of its body, so a thread importing mode
# concurrently could be handed the original pre-swap module -- which has no
# __getattr__ -- and every lazily-exported name raised AttributeError.  It
# uses a PEP 562 module __getattr__ now, so there is no swap to race with.
#
# Must run in a subprocess: the race only exists on a *cold* import.
# --------------------------------------------------------------------------
def check_lazy_module(trials=25):
    import subprocess

    code = """
import threading, traceback
errors = []
barrier = threading.Barrier(16)
names = ["Service", "Worker", "Signal", "Seconds", "get_logger",
         "SupervisorStrategy", "label", "want_seconds"]
def work():
    barrier.wait()
    try:
        import mode
        for name in names:
            getattr(mode, name)
    except BaseException:
        errors.append(traceback.format_exc())
threads = [threading.Thread(target=work) for _ in range(16)]
[t.start() for t in threads]
[t.join() for t in threads]
if errors:
    print(errors[0])
    raise SystemExit(1)
"""
    bad = 0
    first = ""
    for _ in range(trials):
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=RACE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            bad += 1
            first = first or f"child wedged for {RACE_TIMEOUT}s"
            continue
        if proc.returncode:
            bad += 1
            first = first or proc.stdout.strip().splitlines()[-1]
    report_trials(
        "concurrent cold `import mode`",
        bad,
        f"{bad}/{trials} runs had a failing thread"
        + (f" -> {first}" if first else ""),
    )


def main():
    print(f"python: {sys.version.splitlines()[0]}")
    # Same fallback as mode.utils.collections.FREE_THREADED: builds
    # without the attribute (< 3.13) always have the GIL.
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    print(f"GIL enabled: {gil}\n")

    print("-- surfaces verified safe --")
    check_service_subclass_creation()
    check_get_event_loop()
    check_service_thread()
    check_beacon()
    check_managed_user_dict()
    check_lru_thread_safe()

    print("\n-- regression checks (all should be ok) --")
    check_lazy_module()
    check_signal()
    check_cached_property()
    check_service_proxy()
    check_lru_default()
    check_lru_mapping_surface()


if __name__ == "__main__":
    main()
