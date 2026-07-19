import asyncio

import pytest

from mode.threads import ServiceThread


class UntrackedTaskThread(ServiceThread):
    """Mimics a driver library (e.g. a Kafka client) scheduling a task
    directly on ``thread_loop``, bypassing Service's own
    add_future()/@Service.task tracking entirely.
    """

    debug_task: asyncio.Task

    async def on_thread_started(self) -> None:
        self.debug_task = asyncio.ensure_future(self._untracked_sleep())

    async def _untracked_sleep(self) -> None:
        await asyncio.sleep(5.0)


@pytest.mark.asyncio
async def test_ServiceThread__sweeps_untracked_tasks_on_stop():
    # Regression test for #54: a task scheduled on thread_loop outside of
    # Service's own future-tracking must still be cancelled and awaited to
    # completion when the thread stops, instead of being silently
    # abandoned -- which later triggers "coroutine ... was never awaited" /
    # "Task was destroyed but it is pending" warnings at some
    # unpredictable later point (e.g. interpreter shutdown), whenever the
    # GC happens to finalize the orphaned task/coroutine object. Asserting
    # directly on the task's own state (rather than trying to catch that
    # GC-timing-dependent warning) is what actually proves the sweep ran.
    thread = UntrackedTaskThread()
    await thread.start()
    await asyncio.sleep(0.2)  # let on_thread_started's task actually begin
    assert not thread.debug_task.done()

    await thread.stop()

    assert thread.debug_task.done()
    assert thread.debug_task.cancelled()


@pytest.mark.asyncio
async def test_ServiceThread__closes_self_created_thread_loop():
    thread = ServiceThread()
    await thread.start()
    loop = thread.thread_loop
    await thread.stop()

    assert loop.is_closed()


@pytest.mark.asyncio
async def test_ServiceThread__does_not_close_externally_provided_thread_loop():
    external_loop = asyncio.new_event_loop()
    try:
        thread = ServiceThread(thread_loop=external_loop)
        await thread.start()
        await thread.stop()

        assert not external_loop.is_closed()
    finally:
        external_loop.close()
