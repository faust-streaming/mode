import asyncio

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
