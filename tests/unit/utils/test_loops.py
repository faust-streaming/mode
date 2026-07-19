import asyncio
import threading
from unittest.mock import patch

from mode.utils.loops import get_event_loop


def test_get_event_loop__returns_running_loop_when_running():
    async def main():
        return get_event_loop()

    running = asyncio.new_event_loop()
    try:
        got = running.run_until_complete(main())
        assert got is running
    finally:
        running.close()


def test_get_event_loop__reuses_current_loop_when_set():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        assert get_event_loop() is loop
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_get_event_loop__creates_loop_when_none_set():
    # Emulates Python 3.14, where asyncio.get_event_loop() no longer creates
    # a loop implicitly and raises RuntimeError instead.
    asyncio.set_event_loop(None)
    loop = get_event_loop()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert asyncio.get_event_loop() is loop
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_get_event_loop__is_idempotent_without_running_loop():
    asyncio.set_event_loop(None)
    first = get_event_loop()
    try:
        assert get_event_loop() is first
    finally:
        asyncio.set_event_loop(None)
        first.close()


def test_get_event_loop__caches_loop_without_repeated_lookup():
    # The whole point of the thread-local cache: once a loop has been
    # resolved for a thread, later calls on that thread must not re-invoke
    # (and potentially re-raise/re-catch RuntimeError from)
    # asyncio.get_event_loop() again.
    asyncio.set_event_loop(None)
    first = get_event_loop()
    try:
        with patch(
            "mode.utils.loops.asyncio.get_event_loop"
        ) as get_event_loop_mock:
            second = get_event_loop()
        assert second is first
        get_event_loop_mock.assert_not_called()
    finally:
        asyncio.set_event_loop(None)
        first.close()


def test_get_event_loop__caches_per_thread_not_globally():
    # Mode runs a dedicated event loop per ServiceThread worker thread
    # (see mode.threads.ServiceThread), so the cache must be thread-local:
    # one thread's cached loop must never leak into another thread's call.
    asyncio.set_event_loop(None)
    main_loop = get_event_loop()
    other_loop_holder: dict = {}

    def other_thread() -> None:
        other_loop_holder["loop"] = get_event_loop()

    try:
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()

        other_loop = other_loop_holder["loop"]
        assert other_loop is not main_loop
        assert get_event_loop() is main_loop
    finally:
        asyncio.set_event_loop(None)
        main_loop.close()
        other_loop_holder["loop"].close()
