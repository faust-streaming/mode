# Free-threaded Python (PEP 703) support

`mode` supports free-threaded ("no-GIL") CPython. This page records what
was wrong before that was true, how each defect was fixed, and how to
re-check the work.

Everything here was measured on **CPython 3.14.0rc2 free-threading build**
(`python3.14t`, `sys._is_gil_enabled() == False`), with a GIL-enabled
CPython 3.14.0rc2 used as the control. The reproducers live in
`tests/freethreading/stress.py`; the regression tests that keep the fixes
honest live in `tests/functional/test_thread_safety.py` and run on every
leg of the CI matrix.

The "before" numbers are races, so the failure *rates* move between runs —
they are representative single runs, not stable constants. On repeated runs
the free-threaded `cached_property` figure ranged from 104/300 to 164/300,
and the cold-import figure from 14/25 to 18/25. What did not move is which
side of each table failed.

## Status

`mode` is pure Python, so there was never anything to *port* — it installed,
imported and passed its test suite on a free-threaded interpreter from the
start. What free threading changed is that four latent thread-safety defects
stopped being theoretical. One of them crashed the interpreter.

All four are fixed.

| | Free-threaded (before) | Free-threaded (after) | GIL |
|---|---|---|---|
| `pip install mode-streaming` | works (`py3-none-any`) | works | works |
| Import every `mode` module | GIL stays disabled | GIL stays disabled | n/a |
| `pytest tests/unit tests/functional` | passes | passes | passes |
| `LRUCache` under 16 threads | **SIGSEGV** | clean | clean |
| `cached_property` under 16 threads | **duplicate objects** | one object | one object |
| concurrent cold `import mode` | fails 14/25 runs | 0/25 | 0/25 |
| `Signal` under 16 threads | raises 30/30 | 0/30 | 0/30 |
| `mode[uvloop]` | GIL stays disabled | GIL stays disabled | n/a |
| `mode[gevent]` | **GIL re-enabled** | **GIL re-enabled** | n/a |

`mode[gevent]` is the one item that is not fixed, because it cannot be
fixed here — see below.

## What already worked

No packaging work was required. `mode` ships no C extensions, so the
existing `py3-none-any` wheel already installs and runs on `3.13t`/`3.14t`.
Importing every module in the package leaves the GIL disabled, and the core
dependencies (`colorlog`, `croniter`, `mypy_extensions`) are pure Python.

These were stress-tested with 16 concurrent OS threads and found **safe**
as they stood:

- `Service` subclass creation — `__init_subclass__` writing the shared
  `cls._tasks` mapping (`mode/services.py`)
- `ServiceThread` start/stop from many threads concurrently
- `get_event_loop()` — the `threading.local` cache in `mode/utils/loops.py`
  correctly gives each thread its own loop with no cross-thread leakage
- `Node`/beacon tree traversal concurrent with mutation
- `ManagedUserDict` / `FastUserDict` mutation
- `annotations()` / `eval_type()`
- `LocalStack` — already `ContextVar`-based, so correct by construction

## The four defects, and their fixes

### 1. `LRUCache` could segfault the interpreter

**Was: critical. Free-threading-specific.**

`LRUCache.data` was a `collections.OrderedDict` and `thread_safety`
defaulted to `False`, which made the mutex a `nullcontext`. So eviction in
`__setitem__` and iteration in `keys()` ran with no lock at all.

Under the GIL this was benign: 0/20 stress trials raised. On `3.14t` the
same code first raised `RuntimeError: OrderedDict changed size during
iteration` and then **segfaulted** — 4 of 5 runs of a 60-trial loop exited
with SIGSEGV, and a 5th hung.

The cause was `OrderedDict` itself. Repeating the identical concurrent
mutate-and-iterate loop against a bare container:

| container | free-threaded 3.14t |
|---|---|
| `collections.OrderedDict` | SIGSEGV / SIGABRT, 3/3 runs |
| plain `dict` | survives, 3/3 runs |

Free-threaded CPython gives plain `dict` per-object locking; `OrderedDict`'s
C implementation did not get the same treatment, so concurrent mutation
corrupts its internal linked list.

**Fixed** in `mode/utils/collections.py` by:

- Making the mutex mandatory on free-threaded builds. `thread_safety`
  defaults to the new `mode.utils.collections.FREE_THREADED` flag, checked
  at runtime rather than build time so `PYTHON_GIL=1` is respected, and
  passing `thread_safety=False` on such a build now raises `ValueError`
  rather than handing back a structure that can take the interpreter down.
- Snapshotting in `_keys`/`_values`/`_items` instead of holding the mutex
  across `yield`. The old code kept the lock held for as long as the
  *consumer* took to iterate — and forever if the consumer abandoned the
  generator, since the lock was only released when the generator was
  closed. That hazard was latent while the lock defaulted to off; turning
  the lock on by default would have made it real.

### Why not just swap `OrderedDict` for `dict`?

That was the first fix, and it was wrong. `dict` has preserved insertion
order since 3.7 and is memory-safe under free threading, so it looks like a
free win — but `LRUCache`'s hot path is evicting the *oldest* entry, and
that is the one thing `dict` cannot do in O(1). `OrderedDict.popitem(last=
False)` unlinks a node; the `dict` equivalent, `d.pop(next(iter(d)))`, has
to scan past every slot vacated since the last resize.

Steady-state evict-and-insert, 100k operations:

| cache size | `OrderedDict` | `dict` |
|---|---|---|
| 1,000 | 0.043s | 0.089s |
| 10,000 | 0.046s | 0.448s |
| 100,000 | 0.052s | 2.447s |

The gap grows linearly with the cache, because the eviction itself became
O(n). Periodically rebuilding the dict to compact it only softens this to
O(√n) — still ~24x at 100k — so there is no cheap repair. `OrderedDict` is
the right data structure here; the concurrency hazard belongs to the mutex,
not to the choice of container.

`LRUCache` is not used inside `mode` itself; it is exported utility surface
(faust is a consumer), so the blast radius was downstream.

### 2. `cached_property` handed different objects to different threads

**Was: high. Free-threading-specific.**

`cached_property.__get__` was a check-then-act on `obj.__dict__`: try the
key, catch `KeyError`, compute, store. Nothing made that atomic.

| | duplicate-object trials | computes per 300 properties |
|---|---|---|
| GIL 3.14 | 0/300 | 300 |
| free-threaded 3.14t | **104/300** | 419 |

This was not merely wasted work. `ServiceProxy` documents
`@cached_property _service` as *the* way to build the proxied service — it
is how the Faust App is constructed at module level. Racing 16 threads on
`proxy._service`:

| | trials that built/returned >1 `Service` |
|---|---|
| GIL 3.14 | 0/200 |
| free-threaded 3.14t | **198/200** |

So one thread could `start()` one `Service` instance while another held a
different instance, and the later `stop()` never reached the one that was
started.

**Fixed** in `mode/utils/objects.py` with double-checked locking: the
already-cached lookup stays lock-free (a plain dict hit), and only the miss
path takes a per-descriptor `RLock` and re-checks after acquiring.
Contention is therefore limited to first-time initialisation.

Note that stdlib `functools.cached_property` deliberately dropped its lock
in 3.12 and accepts duplicate computation. That trade-off is fine for a
pure value cache; it is not fine for a singleton service handle.

### 3. Concurrent first `import mode` could hand back a half-built module

**Was: high. Pre-existing, but much worse under free threading.** This one
broke the most ordinary thing a user does.

`mode/__init__.py` used the Werkzeug lazy-import trick: define a `_module`
subclass whose `__getattr__` resolves the lazily-exported names, then swap
it into `sys.modules` at the *end* of the module body.

If thread B ran `import mode` while thread A was still executing
`mode/__init__.py`, B could be handed the original, pre-swap module object —
which has no `__getattr__` — so every lazily-exported name raised:

```
AttributeError: module 'mode' has no attribute 'Service'
```

Racing 16 threads on a cold `import mode` followed by attribute access:

| | runs with at least one failing thread |
|---|---|
| GIL 3.14 | 3/25 |
| free-threaded 3.14t | **14/25** |

Instrumenting a failing thread confirmed the mechanism: the object it
imported was a plain `module` while `sys.modules["mode"]` was already the
`_module` instance — the thread held the stale pre-swap object. The
replacement module also carried **no `__spec__`**, which deprived the import
machinery of the `_initializing` flag it would otherwise use to make the
second thread wait.

**Fixed** by dropping the `sys.modules` swap entirely in favour of a
:pep:`562` module-level `__getattr__` (plus a module `__dir__`). PEP 562
landed in 3.7 and mode's floor is 3.10, so the `_module` class existed only
for compatibility that is no longer needed. With no swap, the race cannot
happen — and `sys.modules["mode"]` keeps its real `__spec__`.

### 4. `Signal` mutated its receiver set during iteration

**Was: medium. Pre-existing, not a free-threading regression** — it raised
`RuntimeError: Set changed size during iteration` in 30/30 trials on *both*
builds, so `Signal` had never been thread-safe.

`_get_live_receivers` iterated `self._receivers` (a plain `set`) while
`connect`/`disconnect` added to and discarded from it — and the caller then
discarded dead refs from the same set using the result.

**Fixed** in `mode/signals.py` by iterating a snapshot.

The snapshot must be `list(r)`, **not** `tuple(r)`. This is not stylistic:

| snapshot of a set being mutated by 4 threads | free-threaded 3.14t |
|---|---|
| `tuple(s)` | **8 failures** — `Set changed size during iteration` |
| `list(s)` | 0 failures |
| `set(s)` | 0 failures |
| `s.copy()` | 0 failures |
| `frozenset(s)` | 0 failures |

`list()`, `set()` and `set.copy()` take the source set's per-object lock for
the duration of the copy; `tuple()` falls back to the generic iterator
protocol and does not, so `tuple(r)` raises the very error the snapshot
exists to prevent. The first attempt at this fix used `tuple(r)` and the
stress harness caught it.

### Not fixable here: the `gevent` extra re-enables the GIL

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

This is upstream in gevent, not something `mode` can fix. It is flagged in
`pyproject.toml` next to the extra, and `mode/loop/gevent.py` now warns at
import time on a free-threaded build — the degradation is otherwise silent,
since you keep running and simply are not free-threaded any more. That check
uses the *build* flag (`sysconfig.get_config_var("Py_GIL_DISABLED")`) rather
than `sys._is_gil_enabled()`, which by then already reads `True`.

**Separately: `mode.loop.use("gevent")` is currently broken on every build.**
This has nothing to do with free threading — it fails identically on
GIL-enabled 3.10 and 3.14 with gevent 26.7.0:

```
ImportError: Cannot import 'Loop' from <module 'mode.loop._gevent_loop'>
```

The cause is a self-referential import. `mode/loop/gevent.py` sets
`GEVENT_LOOP=mode.loop._gevent_loop.Loop`, but `mode/loop/_gevent_loop.py`
imports `gevent.core` at module scope in order to subclass
`gevent.core.loop`. Importing it therefore builds a gevent hub, which
resolves `GEVENT_CONFIG.loop`, which imports `mode.loop._gevent_loop` — a
module whose body has not yet reached `class Loop`. Pre-importing the module
does not help, because the cycle is inside its own import.

gevent itself is fine: `gevent.monkey.patch_all()` plus
`asyncio_gevent.EventLoopPolicy` runs an asyncio coroutine correctly. Only
mode's custom `GEVENT_LOOP` hook fails. Presumably gevent used to resolve
that setting lazily and no longer does.

`mode.loop` has no test coverage, which is how this went unnoticed. Fixing
it would mean building `Loop` lazily rather than at module scope.

Rather than repair a backend that cannot work on free-threaded builds
anyway, the gevent loop is **deprecated**: selecting it raises a
`DeprecationWarning` naming the breakage and pointing at `aio`/`uvloop`,
and it is slated for removal in a future major release. Nothing is removed
yet, so this is not a breaking change.

## CI

`3.14t` is part of the `tests.yml` matrix, so the suite — including
`tests/functional/test_thread_safety.py` — runs with the GIL disabled on
every push. `ruff` and `mypy` both run clean on the free-threaded build.

The package advertises
`Programming Language :: Python :: Free Threading :: 2 - Beta`.

### A note on `pytest-run-parallel`

`pytest-run-parallel` installs and runs on `3.14t`, but pointing
`--parallel-threads` at the existing suite is not useful: it reports ~33
failures in `tests/functional/utils/test_collections.py` alone that are
artifacts of tests sharing mutable fixtures and `Mock` objects, not mode
bugs. For example `test_AttributeDictMixin::test_set_get` fails with "DID
NOT RAISE AttributeError" purely because a sibling thread already set the
attribute on the shared object.

Use it selectively on purpose-written thread-safety tests rather than across
the whole suite.

## Reproducing

```sh
uv python install 3.14t
uv venv --python 3.14t .venv-ft
VIRTUAL_ENV=.venv-ft uv pip install -e . -r requirements-tests.txt
.venv-ft/bin/python -m pytest tests/unit tests/functional
.venv-ft/bin/python tests/freethreading/stress.py
```

`tests/freethreading/` is deliberately outside the `testpaths` configured in
`pyproject.toml`, so the heavier probabilistic reproducers are never
collected by a normal `pytest` run. Run the same file under a GIL-enabled
interpreter to see the control numbers.
