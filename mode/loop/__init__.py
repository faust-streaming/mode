"""AsyncIO event loop implementations.

This contains a registry of different AsyncIO loop implementations
to be used with Mode.

The choices available are:

aio **default**
    Normal `asyncio` event loop policy.

### eventlet

Use [`eventlet`](https://pypi.org/project/eventlet) as the event loop.

This uses [`aioeventlet`](https://pypi.org/project/aioeventlet) and will apply the
[`eventlet`](https://pypi.org/project/eventlet) monkey-patches.

To enable execute the following as the first thing that happens
when your program starts (e.g. add it as the top import of your
entrypoint module):

```python
import mode.loop
mode.loop.use('eventlet')
```

### gevent **deprecated, currently broken**

!!! warning
    This backend is unmaintained, has no test coverage, and does not
    presently work on *any* interpreter: selecting it raises
    `ImportError: Cannot import 'Loop' from mode.loop._gevent_loop` with
    current gevent releases, on GIL-enabled and free-threaded builds
    alike.  It also re-enables the GIL on free-threaded builds, because
    `gevent.libev.corecext` does not declare that it is safe without it.

    Selecting it raises a `DeprecationWarning`, and it will be removed in
    a future major release.  Use `aio` (the default) or `uvloop`.

Use [`gevent`](https://pypi.org/project/gevent) as the event loop.

This uses [`aiogevent`](https://pypi.org/project/aiogevent) (+modifications) and will apply the
[`gevent`](https://pypi.org/project/gevent) monkey-patches.

This choice enables you to run blocking Python code as if they
have invisible `async/await` syntax around it (NOTE: C extensions are
not usually gevent compatible).

To enable execute the following as the first thing that happens
when your program starts (e.g. add it as the top import of your
entrypoint module):

```python
import mode.loop
mode.loop.use('gevent')
```

### uvloop

Event loop using [`uvloop`](https://pypi.org/project/uvloop).

To enable execute the following as the first thing that happens
when your program starts (e.g. add it as the top import of your
entrypoint module):

```python
import mode.loop
mode.loop.use('uvloop')
```
"""

import importlib
import warnings
from collections.abc import Mapping
from typing import Optional

__all__ = ["LOOPS", "use"]

LOOPS: Mapping[str, Optional[str]] = {
    "aio": None,
    "eventlet": "mode.loop.eventlet",
    "gevent": "mode.loop.gevent",
    "uvloop": "mode.loop.uvloop",
}

#: Backends that still resolve, but should not be used in new code.
DEPRECATED_LOOPS: Mapping[str, str] = {
    "gevent": (
        "The gevent loop backend is deprecated and currently broken: it is "
        "unmaintained, has no test coverage, and importing it fails with "
        "current gevent releases on every interpreter (see "
        "docs/free-threading.md).  It also re-enables the GIL on "
        "free-threaded builds.  Use the 'aio' or 'uvloop' backend instead. "
        "It will be removed in a future major release."
    )
}


def use(loop: str) -> None:
    """Specify the event loop to use as a string.

    Loop must be one of: aio, eventlet, gevent, uvloop.

    Note:
        `gevent` is deprecated and currently broken -- selecting it raises
        a `DeprecationWarning` and then fails to import.  See the module
        docstring.
    """
    deprecated = DEPRECATED_LOOPS.get(loop)
    if deprecated is not None:
        # stacklevel=2 attributes this to the caller, so it is actually
        # shown when selected from an entrypoint module.
        warnings.warn(deprecated, DeprecationWarning, stacklevel=2)
    mod = LOOPS.get(loop, loop)
    if mod is not None:
        importlib.import_module(mod)
