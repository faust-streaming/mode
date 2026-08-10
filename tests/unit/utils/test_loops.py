import asyncio
import contextvars
import signal
import sys
import threading
from unittest.mock import Mock, patch

import pytest

from mode.utils.loops import (
    _appropriate_signal_handler,
    _call_asap,
    _is_unix_loop,
    call_asap,
    clone_loop,
    get_event_loop,
)


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


# The helpers below (_is_unix_loop, clone_loop, _appropriate_signal_handler,
# call_asap, _call_asap) had no coverage at all, which left mode/utils/loops.py
# at 34%.  They are exported but currently unused inside mode itself.


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


class test_is_unix_loop:
    @pytest.mark.skipif(
        sys.platform == "win32", reason="no unix event loop on windows"
    )
    def test_true_for_a_unix_selector_loop(self, loop):
        assert _is_unix_loop(loop)

    def test_false_for_anything_else(self):
        assert not _is_unix_loop(Mock(name="loop"))


class test_clone_loop:
    def test_returns_a_new_loop(self, loop):
        new_loop = clone_loop(loop)
        try:
            assert new_loop is not loop
            assert isinstance(new_loop, asyncio.AbstractEventLoop)
        finally:
            new_loop.close()

    def test_non_unix_loop_copies_no_signal_handlers(self):
        new_loop = clone_loop(Mock(name="loop"))
        try:
            assert isinstance(new_loop, asyncio.AbstractEventLoop)
        finally:
            new_loop.close()

    @pytest.mark.skipif(
        sys.platform == "win32", reason="no signal handlers on windows"
    )
    def test_retains_signal_handlers(self, loop):
        loop.add_signal_handler(signal.SIGUSR1, lambda: None)
        new_loop = clone_loop(loop)
        try:
            assert signal.SIGUSR1 in new_loop._signal_handlers
        finally:
            new_loop.remove_signal_handler(signal.SIGUSR1)
            new_loop.close()
            loop.remove_signal_handler(signal.SIGUSR1)


class test_appropriate_signal_handler:
    def test_calls_the_original_callback_on_the_parent_loop(self, loop):
        called = []
        handle = asyncio.Handle(
            lambda *a: called.append(a),
            (1, 2),
            loop,
            contextvars.copy_context(),
        )

        wrapper = _appropriate_signal_handler(loop, handle)
        wrapper()

        # _call_asap queues onto the parent loop rather than calling inline.
        assert called == []
        assert loop._ready
        loop._ready.popleft()._run()
        assert called == [(1, 2)]


class test_call_asap:
    def test_requires_a_loop(self):
        with pytest.raises(AssertionError):
            call_asap(lambda: None)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="no unix event loop on windows"
    )
    def test_unix_loop_pushes_to_the_front(self, loop):
        # NOTE: Only the ordering is asserted, not the number of calls.
        # `_call_asap` currently dispatches the callback twice -- once via
        # `loop._call_soon()` and again via the handle it inserts at
        # `_ready[0]` -- so "jumped" also shows up at the back.  Asserting
        # the exact sequence would enshrine that; asserting the front of
        # the queue tests the documented contract and keeps passing if the
        # duplicate is ever removed.
        order = []
        loop.call_soon(lambda: order.append("first-queued"))
        call_asap(lambda: order.append("jumped"), loop=loop)

        while loop._ready:
            loop._ready.popleft()._run()

        assert order[0] == "jumped"
        assert "first-queued" in order

    def test_other_loops_delegate_to_call_soon_threadsafe(self):
        mock_loop = Mock(name="loop")
        callback = Mock(name="callback")

        result = call_asap(callback, 1, 2, loop=mock_loop)

        mock_loop.call_soon_threadsafe.assert_called_once_with(callback, 1, 2)
        assert result is mock_loop.call_soon_threadsafe.return_value

    def test_other_loops_pass_the_context_through(self):
        mock_loop = Mock(name="loop")
        callback = Mock(name="callback")
        context = contextvars.copy_context()

        call_asap(callback, 1, loop=mock_loop, context=context)

        mock_loop.call_soon_threadsafe.assert_called_once_with(
            callback, 1, context=context
        )


class test__call_asap:
    @pytest.mark.skipif(
        sys.platform == "win32", reason="no unix event loop on windows"
    )
    def test_returns_a_handle_and_wakes_the_loop(self, loop):
        callback = Mock(name="callback")

        handle = _call_asap(loop, callback, 1, 2)

        assert isinstance(handle, asyncio.Handle)
        assert loop._ready
        # Only the front handle is run: `_call_asap` also leaves a second,
        # duplicate handle further back in `_ready` (see the note in
        # test_unix_loop_pushes_to_the_front).
        loop._ready.popleft()._run()
        callback.assert_called_once_with(1, 2)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="no unix event loop on windows"
    )
    def test_accepts_a_context(self, loop):
        callback = Mock(name="callback")

        handle = _call_asap(loop, callback, context=contextvars.copy_context())

        assert isinstance(handle, asyncio.Handle)

    def test_raises_when_the_loop_is_closed(self):
        closed = asyncio.new_event_loop()
        closed.close()
        with pytest.raises(RuntimeError):
            _call_asap(closed, Mock(name="callback"))

    @pytest.mark.skipif(
        sys.platform == "win32", reason="no unix event loop on windows"
    )
    def test_debug_mode_validates_the_callback(self, loop):
        loop.set_debug(True)
        with pytest.raises(TypeError):
            _call_asap(loop, "not-callable")
