"""Enable [`gevent`](https://pypi.org/project/gevent) support for `asyncio`."""

import asyncio
import os
import warnings
from typing import Optional, cast

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
loop = asyncio.get_event_loop_policy().get_event_loop()
