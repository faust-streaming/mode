# Free-threaded Python (PEP 703) support

Status of `mode` on free-threaded ("no-GIL") CPython builds, and what
remains to be done.

Everything below was measured on **CPython 3.14.0rc2 free-threading build**
(`python3.14t`, `sys._is_gil_enabled() == False`), with a GIL-enabled
CPython 3.14.0rc2 used as the control. The reproducers live in
`tests/freethreading/stress.py`.

## Summary

`mode` is pure Python, so there is nothing to port: it installs, imports
and passes its whole test suite on a free-threaded interpreter today. What
free threading changes is that three latent thread-safety defects stop
being theoretical. One of them crashes the interpreter.

| | Free-threaded | GIL |
|---|---|---|
| `pip install mode-streaming` | works (`py3-none-any`) | works |
| Import every `mode` module | GIL stays disabled | n/a |
| `pytest tests/unit tests/functional` | 757 passed, 2 skipped | 757 passed, 2 skipped |
| `LRUCache` under 16 threads | **SIGSEGV** | fine |
| `cached_property` under 16 threads | **duplicate objects** | fine |
| concurrent first `import mode` | fails 14/25 runs | fails 3/25 runs |
| `Signal` under 16 threads | raises | raises (pre-existing) |
| `mode[uvloop]` | GIL stays disabled | n/a |
| `mode[gevent]` | **GIL re-enabled** | n/a |

## What already works

No packaging work is required. `mode` ships no C extensions, so the
existing `py3-none-any` wheel already installs and runs on `3.13t`/`3.14t`.
Importing every module in the package leaves the GIL disabled, and the core
dependencies (`colorlog`, `croniter`, `mypy_extensions`) are pure Python.
The full test suite passes unmodified.

These were stress-tested with 16 concurrent OS threads and found **safe**:

- `Service` subclass creation — `__init_subclass__` writing the shared
  `cls._tasks` mapping (`mode/services.py:527-553`)
- `ServiceThread` start/stop from many threads concurrently
- `get_event_loop()` — the `threading.local` cache in
  `mode/utils/loops.py:15` correctly gives each thread its own loop with no
  cross-thread leakage
- `Node`/beacon tree traversal concurrent with mutation
- `ManagedUserDict` / `FastUserDict` mutation
- `annotations()` / `eval_type()`
- `LocalStack` — already `ContextVar`-based, so correct by construction

## Findings

### 1. `LRUCache` can segfault the interpreter — free-threading-specific

**Severity: critical.**

`LRUCache.data` is a `collections.OrderedDict` and `thread_safety` defaults
to `False`, which makes `self._mutex` a `nullcontext`
(`mode/utils/collections.py:449-455`, `:523-526`). So `__setitem__` —
which evicts via `self.data.pop(next(iter(self.data)))`
(`mode/utils/collections.py:474-479`) — and `keys()`, which iterates the
same dict (`mode/utils/collections.py:489-491`), run with no lock at all.

Under the GIL this is benign: 0/20 stress trials raised. On `3.14t` the
same code first raises `RuntimeError: OrderedDict changed size during
iteration` and then **segfaults**: 4 of 5 runs of a 60-trial loop exited
with SIGSEGV, and a 5th hung.

The cause was isolated to `OrderedDict` itself. Repeating the identical
concurrent mutate-and-iterate loop against a bare container:

| container | free-threaded 3.14t |
|---|---|
| `collections.OrderedDict` | SIGSEGV / SIGABRT, 3/3 runs |
| plain `dict` | survives, 3/3 runs |

Free-threaded CPython gives plain `dict` per-object locking; `OrderedDict`'s
C implementation did not get the same treatment, so concurrent mutation
corrupts its internal linked list.

Two independent fixes, either of which is sufficient:

- Back `LRUCache` with a plain `dict`. Insertion order has been guaranteed
  since 3.7, and the only `OrderedDict`-specific API used is
  `popitem(last=...)`, which maps to `d.popitem()` for `last=True` and
  `d.pop(next(iter(d)))` for `last=False`.
- Default `thread_safety=True` on free-threaded builds. The existing mutex
  path is sound — `LRUCache(thread_safety=True)` passed the stress test
  cleanly — it is just off by default.

`LRUCache` is not used inside `mode` itself; it is exported utility surface
(faust is a consumer), so the blast radius is downstream.

### 2. `cached_property` hands different objects to different threads — free-threading-specific

**Severity: high.**

`cached_property.__get__` (`mode/utils/objects.py:685-694`) is a
check-then-act on `obj.__dict__`: try the key, catch `KeyError`, compute,
store. Nothing makes that atomic.

| | duplicate-object trials | computes per 300 properties |
|---|---|---|
| GIL 3.14 | 0/300 | 300 |
| free-threaded 3.14t | **104/300** | 419 |

This is not merely wasted work. `ServiceProxy` documents
`@cached_property _service` as *the* way to build the proxied service
(`mode/proxy.py:17-35`) — it is how the Faust App is constructed at module
level. A reproducer that races 16 threads on `proxy._service`:

| | trials that built/returned >1 `Service` |
|---|---|
| GIL 3.14 | 0/200 |
| free-threaded 3.14t | **198/200** |

So one thread can `start()` one `Service` instance while another thread
holds a different instance, and the later `stop()` never reaches the one
that was started.

Note that stdlib `functools.cached_property` deliberately dropped its lock
in 3.12 and accepts duplicate computation. That trade-off is fine for a
pure value cache; it is not fine for a singleton service handle. The fix is
double-checked locking in `cached_property.__get__` (a per-instance or
per-descriptor lock), or failing that, making `ServiceProxy._service`
guard itself.

### 3. Concurrent first `import mode` can hand back a half-built module — pre-existing, much worse under free threading

**Severity: high.** This one breaks the most ordinary thing a user does.

`mode/__init__.py` uses the Werkzeug lazy-import trick: it defines a
`_module` subclass with a `__getattr__` that resolves the lazily-exported
names, then swaps it into `sys.modules` at the *end* of the module body
(`mode/__init__.py:88-129`):

```python
new_module = sys.modules[__name__] = _module(__name__)
new_module.__dict__.update({"__file__": ..., "__path__": ..., ...})
```

If thread B runs `import mode` while thread A is still executing
`mode/__init__.py`, B can be handed the original, pre-swap module object —
which has no `__getattr__` yet — so every lazily-exported name raises:

```
AttributeError: module 'mode' has no attribute 'Service'
```

Racing 16 threads on a cold `import mode` followed by attribute access:

| | runs with at least one failing thread |
|---|---|
| GIL 3.14 | 3/25 |
| free-threaded 3.14t | **14/25** |

Instrumenting a failing thread confirms the mechanism: the object it
imported is a plain `module` (`type(mode).__name__ == "module"`) while
`sys.modules["mode"]` is already the `_module` instance — the thread holds
the stale pre-swap object. The replacement module also carries **no
`__spec__`** (`sys.modules["mode"].__spec__ is None`), which is what
deprives the import machinery of the `_initializing` flag it would
otherwise use to make the second thread wait.

The fix is to drop the `sys.modules` swap entirely and use a PEP 562
module-level `__getattr__`, which needs no module replacement and is
therefore race-free. PEP 562 landed in 3.7 and mode's floor is 3.10, so the
`_module` class exists only for compatibility that is no longer needed:

```python
def __getattr__(name: str) -> Any:
    if name in object_origins:
        module = __import__(object_origins[name], None, None, [name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### 4. `Signal` mutates its receiver set during iteration — pre-existing

**Severity: medium. Not a free-threading regression.**

`_get_live_receivers` iterates `self._receivers` (a plain `set`)
(`mode/signals.py:157-167`) while `connect`/`disconnect` add and discard on
it (`mode/signals.py:120`, `:132`). Racing those raises
`RuntimeError: Set changed size during iteration` in **30/30 trials on both
builds** — so `Signal` has never been thread-safe. Free threading only
makes concurrent use likely enough to hit it in practice.

Fix: iterate a snapshot, e.g. `for href in tuple(r):`.

### 5. The `gevent` extra re-enables the GIL — packaging

| extra | result on `3.14t` |
|---|---|
| `mode[uvloop]` | uvloop 0.22.1 imports and runs, GIL stays disabled |
| `mode[eventlet]` | imports, GIL stays disabled (eventlet prints its own migrate-away notice) |
| `mode[gevent]` | **GIL re-enabled at import** |

Installing `mode[gevent]` silently downgrades a free-threaded interpreter
back to GIL semantics:

```
RuntimeWarning: The global interpreter lock (GIL) has been enabled to load
module 'gevent.libev.corecext', which has not declared that it can run
safely without the GIL.
```

This is upstream in gevent, not something `mode` can fix — it should be
documented as an unsupported combination.

## Suggested order of work

1. Fix `LRUCache` (finding 1) — it is an interpreter crash.
2. Fix `cached_property` (finding 2) — silent correctness bug for
   `ServiceProxy`, and therefore for faust.
3. Convert `mode/__init__.py` to a PEP 562 module `__getattr__`
   (finding 3) — breaks plain `import mode`, and is a real bug under the
   GIL too.
4. Snapshot the `Signal` receiver set (finding 4) — cheap, and also
   pre-existing.
5. Add `3.14t` to the `tests.yml` matrix. `actions/setup-python` accepts
   the `3.14t` version string directly.
6. Add a trove classifier once 1-4 land:
   `Programming Language :: Python :: Free Threading :: 2 - Beta`
   (the `Free Threading :: N - ...` classifiers are registered in
   `trove-classifiers`).
7. Document `mode[gevent]` as incompatible with free-threaded builds.

### A note on `pytest-run-parallel`

`pytest-run-parallel` installs and runs on `3.14t`, but pointing
`--parallel-threads` at the existing suite is not useful: it reports ~33
failures in `tests/functional/utils/test_collections.py` alone that are
artifacts of tests sharing mutable fixtures and `Mock` objects, not
mode bugs. For example
`test_AttributeDictMixin::test_set_get` fails with "DID NOT RAISE
AttributeError" purely because a sibling thread already set the attribute
on the shared object.

Use it selectively on purpose-written thread-safety tests rather than
across the whole suite.

## Reproducing

```sh
uv python install 3.14t
uv venv --python 3.14t .venv-ft
VIRTUAL_ENV=.venv-ft uv pip install -e . -r requirements-tests.txt
.venv-ft/bin/python tests/freethreading/stress.py
```

`tests/freethreading/` is deliberately outside the `testpaths` configured
in `pyproject.toml`, so the crash reproducers are never collected by a
normal `pytest` run. Run the same file under a GIL-enabled interpreter to
see the control numbers.
