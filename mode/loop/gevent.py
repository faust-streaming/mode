"""Enable [`gevent`](https://pypi.org/project/gevent) support for `asyncio`.

!!! warning "Deprecated and currently broken"
    This loop backend is unmaintained, has no test coverage, and does not
    presently work on *any* interpreter -- importing it raises
    `ImportError: Cannot import 'Loop' from mode.loop._gevent_loop` with
    current gevent releases, on GIL-enabled and free-threaded builds
    alike.  See `docs/free-threading.md` for the diagnosis.

    Use the `aio` (default) or `uvloop` backend instead.  This module will
    be removed in a future major release.
"""

import asyncio
import os
import sysconfig
import warnings
from typing import Optional, cast

from mode.utils.loops import get_event_loop

# NOTE: The DeprecationWarning for this backend is raised by
# `mode.loop.use()`, not here.  A module-level `warnings.warn` is
# attributed to whichever importlib frame executed the module body, and
# DeprecationWarning is filtered out everywhere except `__main__` -- so it
# would never be shown.  Raising it from `use()` with stacklevel=2 puts it
# on the caller instead, which is where users select the backend.

# NOTE: Deliberately the *build* flag, not `sys._is_gil_enabled()`.  The
# runtime check would already read True by the time gevent has been
# imported below, which is exactly the situation being reported.
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    warnings.warn(
        "The gevent loop is not usable on free-threaded builds: importing "
        "gevent re-enables the GIL (gevent.libev.corecext does not declare "
        "that it is safe without it), so selecting this loop silently gives "
        "up free threading for the whole process.  Use the 'aio' or 'uvloop' "
        "loop to keep the GIL disabled.",
        RuntimeWarning,
        stacklevel=2,
    )

os.environ["GEVENT_LOOP"] = "mode.loop._gevent_loop.Loop"
try:
    import gevent
    import gevent.monkey
except ImportError:
    raise ImportError(
        "Gevent loop requires the gevent library: pip install gevent"
    ) from None
gevent.monkey.patch_all()

try:
    import psycopg2  # noqa: F401
except ImportError:
    pass
else:
    try:
        import psycogreen.gevent
    except ImportError:
        warnings.warn(
            "psycopg2 installed, but not psycogreen: pg will be blocking",
            stacklevel=1,
        )
    else:
        psycogreen.gevent.patch_psycopg()

try:
    import asyncio_gevent
except ImportError:
    raise
    raise ImportError(
        "Gevent loop requires the aiogevent library: pip install aiogevent"
    ) from None


if asyncio._get_running_loop() is not None:
    raise RuntimeError("Event loop created before importing gevent loop!")


class Policy(asyncio_gevent.EventLoopPolicy):  # type: ignore
    """Custom gevent event loop policy."""

    _loop: Optional[asyncio.AbstractEventLoop] = None

    def get_event_loop(self) -> asyncio.AbstractEventLoop:
        # asyncio_gevent raises an error here current_thread() is not MainThread,
        # but gevent monkey patches current_thread, so it's not a good check.
        loop = self._loop
        if loop is None:
            loop = self._loop = self.new_event_loop()
        return cast(asyncio.AbstractEventLoop, loop)


policy = Policy()
asyncio.set_event_loop_policy(policy)
loop = get_event_loop()
