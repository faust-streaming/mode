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

### gevent

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
from collections.abc import Mapping
from typing import Optional

__all__ = ["LOOPS", "use"]

LOOPS: Mapping[str, Optional[str]] = {
    "aio": None,
    "eventlet": "mode.loop.eventlet",
    "gevent": "mode.loop.gevent",
    "uvloop": "mode.loop.uvloop",
}


def use(loop: str) -> None:
    """Specify the event loop to use as a string.

    Loop must be one of: aio, eventlet, gevent, uvloop.
    """
    mod = LOOPS.get(loop, loop)
    if mod is not None:
        importlib.import_module(mod)
