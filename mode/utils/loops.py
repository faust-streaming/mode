"""Event loop utilities."""

import asyncio
import threading
from typing import Any, Callable, Optional

__all__ = ["call_asap", "clone_loop", "get_event_loop"]

#: Per-thread cache of the loop resolved by :func:`get_event_loop` for when
#: there is no *running* loop.  ``threading.local`` gives every thread its
#: own attribute storage for free (no cross-thread leakage, no explicit
#: cleanup required when a thread exits) -- required for correctness, not
#: just performance, since Mode creates and runs a dedicated event loop per
#: :class:`~mode.threads.ServiceThread` worker thread.
_current_loop = threading.local()


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Return the current event loop, creating one if necessary.

    :func:`asyncio.get_event_loop` used to create and register an event loop
    for the main thread when none was set.  That implicit behaviour was
    deprecated in Python 3.10 and removed in Python 3.12/3.14, where both
    :func:`asyncio.get_event_loop` and
    ``asyncio.get_event_loop_policy().get_event_loop()`` raise
    :exc:`RuntimeError` when there is no current event loop.

    Mode accesses ``Service.loop`` (and other helpers) outside of a running
    loop -- e.g. at import time, when agents/services are declared at module
    level -- so it needs the historical "get or create" semantics.  This
    restores them in a way that works across Python 3.9-3.14.

    Whether a loop is currently *running* can change on every call (that's
    the whole point of an event loop), so :func:`asyncio.get_running_loop`
    must always be re-checked and can't be cached. When nothing is running,
    though, the resolved loop is stable for the lifetime of the thread that
    resolved it, so it's cached in thread-local storage: once a thread has
    created (or found) its loop, later calls on that thread return the
    cached reference directly instead of re-running the
    ``asyncio.get_event_loop()``-raises-then-create dance every time.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass

    loop: Optional[asyncio.AbstractEventLoop] = getattr(
        _current_loop, "loop", None
    )
    if loop is not None and not loop.is_closed():
        return loop

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    _current_loop.loop = loop
    return loop


def _is_unix_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        from asyncio import unix_events
    except ImportError:
        return False
    else:
        return isinstance(loop, unix_events._UnixSelectorEventLoop)


def clone_loop(loop: asyncio.AbstractEventLoop) -> asyncio.AbstractEventLoop:
    """Clone loop retaining signal handlers."""
    new_loop = asyncio.new_event_loop()
    if _is_unix_loop(loop):
        for signum, handle in loop._signal_handlers.items():  # type: ignore
            new_loop.add_signal_handler(
                signum, _appropriate_signal_handler(loop, handle)
            )
    return new_loop


def _appropriate_signal_handler(
    parent_loop: asyncio.AbstractEventLoop, handle: asyncio.Handle
) -> Callable:
    callback = handle._callback  # type: ignore
    context = getattr(handle, "_context", None)  # CPython 3.7+
    callback_args = handle._args

    def _call_using_parent_loop() -> None:
        _call_asap(parent_loop, callback, *callback_args, context=context)

    return _call_using_parent_loop


def call_asap(
    callback: Callable,
    *args: Any,
    context: Any = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> asyncio.Handle:
    """Call function asap by pushing at the front of the line."""
    assert loop
    if _is_unix_loop(loop):
        return _call_asap(loop, callback, *args, context=context)
    if context is not None:
        return loop.call_soon_threadsafe(  # type: ignore
            callback, *args, context=context
        )
    return loop.call_soon_threadsafe(callback, *args)


def _call_asap(
    loop: Any, callback: Callable, *args: Any, context: Any = None
) -> asyncio.Handle:
    loop._check_closed()
    if loop._debug:
        loop._check_callback(callback, "call_soon_threadsafe")
    loop._call_soon(callback, args, context)
    if context is not None:
        handle = asyncio.Handle(callback, list(args), loop, context)  # type: ignore
    else:
        handle = asyncio.Handle(callback, list(args), loop)
    if handle._source_traceback:  # type: ignore
        del handle._source_traceback[-1]  # type: ignore

    loop._ready.insert(0, handle)

    if handle._source_traceback:  # type: ignore
        del handle._source_traceback[-1]  # type: ignore
    loop._write_to_self()
    return handle
