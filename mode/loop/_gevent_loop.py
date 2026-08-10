"""Gevent loop customizations."""

import asyncio
from typing import Any, Optional

import gevent.core

from mode.utils.loops import get_event_loop


class Loop(gevent.core.loop):  # type: ignore
    """Gevent core event loop modifed to support `asyncio`."""

    _aioloop_loop: Optional[asyncio.AbstractEventLoop] = None

    def run_callback(self, *args: Any, **kwargs: Any) -> None:
        if self._aioloop_loop is None:
            self._aioloop_loop = get_event_loop()
        gevent.spawn_later(0.0, self._aioloop_loop._run_once)  # type: ignore
        super().run_callback(*args, **kwargs)
