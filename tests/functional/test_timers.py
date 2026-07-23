import asyncio
from contextlib import asynccontextmanager
from functools import reduce
from itertools import chain
from typing import NamedTuple, Optional
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest

from mode.timers import Timer
from mode.utils.aiter import aslice


@pytest.mark.asyncio
async def test_Timer_real_run():
    i = 0
    async for sleep_time in Timer(0.1, sleep=asyncio.sleep):
        assert sleep_time == pytest.approx(0.1, 2e-1)
        if i > 10:
            break
        i += 1


def test_Timer_interval__setter_recomputes_derived_values():
    timer = Timer(1.0, name="test")
    assert timer.interval == 1.0
    assert timer.interval_s == pytest.approx(1.0)
    assert timer.max_drift == pytest.approx(0.30)  # min(1.0 * 0.30, 1.2)
    assert timer.min_interval_s == pytest.approx(0.9)  # 1.0 - 0.1
    assert timer.max_interval_s == pytest.approx(1.1)  # 1.0 + 0.1

    # Reassigning the interval refreshes every interval-derived value so
    # the timer is fully retuned, not just its nominal interval.
    timer.interval = 10.0
    assert timer.interval == 10.0
    assert timer.interval_s == pytest.approx(10.0)
    assert timer.max_drift == pytest.approx(1.2)  # min(10.0 * 0.30, 1.2)
    assert timer.min_interval_s == pytest.approx(9.9)  # 10.0 - 0.1
    assert timer.max_interval_s == pytest.approx(10.1)  # 10.0 + 0.1


def test_Timer_interval__small_interval_collapses_bounds():
    # An interval at or under the drift-correction window cannot be
    # corrected, so the bounds collapse onto the interval itself -- this
    # must still hold when the interval is set at runtime.
    timer = Timer(1.0, name="test")
    timer.interval = 0.05
    assert timer.interval_s == pytest.approx(0.05)
    assert timer.min_interval_s == pytest.approx(0.05)
    assert timer.max_interval_s == pytest.approx(0.05)
    assert timer.max_drift == pytest.approx(0.015)  # min(0.05 * 0.30, 1.2)


@pytest.mark.asyncio
async def test_Timer_interval__can_be_changed_at_runtime():
    # After the interval is changed live, tick()/adjust_interval() must use
    # the NEW interval and its recomputed bounds. The induced drift is
    # large, so the next sleep is clamped up to the new max_interval_s
    # (10.1); with the original 1s interval it would clamp to 1.1 instead,
    # so the asserted value only holds if the change took effect.
    clock = Mock()
    clock.side_effect = [
        9.0,  # __init__ epoch
        10.0,
        10.5,  # iter 1 (first tick): returns interval_s, slept 0.5s
        11.0,
        12.0,  # iter 2: drift computed against the new interval
    ]
    sleep = AsyncMock()
    timer = Timer(1.0, name="test", clock=clock, sleep=sleep)
    it = timer.__aiter__()

    with patch("mode.timers.logger"):
        first = await it.__anext__()
        assert first == pytest.approx(1.0)  # still the original cadence

        timer.interval = 10.0  # retune live, mid-iteration

        second = await it.__anext__()
        # 0.5s slept vs the new 10.0s interval is a big positive drift, so
        # adjust_interval clamps up to the NEW max_interval_s (10.1).
        assert second == pytest.approx(10.1)

    await it.aclose()


class Interval(NamedTuple):
    interval: float
    wakeup_time: float
    yield_time: float
    expected_new_interval: float


class test_Timer:
    # first clock value
    epoch = 9.0

    # the timer interval (how long we sleep between each iteration)
    interval = 1.0

    # how much we skew drifting intervals by.
    # early test will do (interval - skew)
    # late test will do (interval + skew)
    skew = 0.3

    # how long it takes to yield
    # Yield gives control back to the event loop, runs other tasks
    # and calls any callback associated with the timer, so this
    # time could be long in some cases.
    default_yield_s = 0.01

    @pytest.fixture
    def clock(self):
        clock = Mock()
        clock.return_value = self.epoch
        return clock

    @pytest.fixture
    def sleep(self):
        return AsyncMock()

    @pytest.fixture
    def timer(self, *, clock, sleep) -> Timer:
        return Timer(self.interval, name="test", clock=clock, sleep=sleep)

    @pytest.fixture
    def first_interval(self):
        return self.new_interval()

    @pytest.mark.asyncio
    async def test_too_early(self, *, clock, timer, first_interval):
        interval = self.interval
        skew = self.skew
        intervals = [
            first_interval,  # 1st interval
            (None, None),  # 2nd interval
            (None, None),  # 3rd interval
            (interval - skew, None),  # 4th interval: sleep too short
            (None, interval + skew),  # 5th interval: overlaps
            (None, None),  # 6th interval
        ]
        async with self.assert_timer(timer, clock, intervals) as logger:
            logger.info.assert_called_once_with(
                "Timer %s woke up too early, with a drift "
                "of -%r runtime=%r sleeptime=%r",
                "test",
                ANY,
                ANY,
                ANY,
            )
            assert timer.drifting == 1
            assert timer.drifting_early == 1
            assert not timer.drifting_late

    @pytest.mark.asyncio
    async def test_too_late(self, *, clock, timer, first_interval):
        interval = self.interval
        skew = self.skew
        intervals = [
            first_interval,  # 1st interval
            (None, None),  # 2nd interval
            (None, None),  # 3rd interval
            (interval + skew, None),  # 4th interval: sleep too long
            (None, interval + skew),  # 5th interval: overlaps
            (None, None),  # 6th interval
        ]
        async with self.assert_timer(timer, clock, intervals) as logger:
            logger.info.assert_called_once_with(
                "Timer %s woke up too late, with a drift "
                "of +%r runtime=%r sleeptime=%r",
                "test",
                ANY,
                ANY,
                ANY,
            )
            assert timer.drifting == 1
            assert timer.drifting_late == 1
            assert not timer.drifting_early

    @asynccontextmanager
    async def assert_timer(self, timer, clock, interval_tuples):
        intervals = self.build_intervals(timer, *interval_tuples)
        print(intervals)
        clock_values = self.to_clock_values(*intervals)
        assert len(clock_values) == len(intervals) * 2
        clock.side_effect = clock_values

        with patch("mode.timers.logger") as logger:
            await self.assert_intervals(timer, intervals)
            yield logger

    def new_interval(
        self,
        interval: Optional[float] = None,
        wakeup_time: Optional[float] = None,
        yield_time: Optional[float] = None,
        expected_new_interval: Optional[float] = None,
    ) -> Interval:
        if interval is None:
            interval = self.interval
        if wakeup_time is None:
            wakeup_time = self.epoch + interval
        if yield_time is None:
            yield_time = wakeup_time + interval + self.default_yield_s
        if expected_new_interval is None:
            expected_new_interval = interval
        return Interval(
            interval, wakeup_time, yield_time, expected_new_interval
        )

    def to_next_interval(
        self,
        timer: Timer,
        interval: Interval,
        sleep_time: Optional[float] = None,
        yield_time: float = 0.1,
        expected_new_interval: Optional[float] = None,
    ) -> Interval:
        if sleep_time is None:
            sleep_time = interval.interval + 0.001
        if yield_time is None:
            yield_time = self.default_yield_s
        next_wakeup_time = interval.yield_time + self.default_yield_s
        next_yield_time = next_wakeup_time + sleep_time
        time_spent_sleeping = interval.yield_time - interval.wakeup_time
        drift = interval.interval - time_spent_sleeping
        if expected_new_interval is None:
            expected_new_interval = timer.adjust_interval(drift)
        return Interval(
            interval=interval.interval,
            wakeup_time=next_wakeup_time,
            yield_time=next_yield_time,
            expected_new_interval=expected_new_interval,
        )

    def interval_to_clock_sequence(self, interval: Interval) -> list[float]:
        # Timer calls clock() twice per iteration,
        # so in an interval this provides the clock for wakeup time
        # and the yield time.
        return [interval.wakeup_time, interval.yield_time]

    def to_clock_values(self, *intervals: Interval) -> list[float]:
        return list(chain(*map(self.interval_to_clock_sequence, intervals)))

    def build_intervals(
        self,
        timer: Timer,
        first_interval: Interval,
        *values: tuple[float, float],
    ) -> list[Interval]:
        """Build intervals from tuples of ``(sleep_time, yield_time)``.

        If a tuple is missing (is None), then default values
        will be taken from the previous interval.
        """

        intervals = [first_interval]

        def on_reduce(
            previous_interval: Interval, tup: tuple[float, float]
        ) -> Interval:
            sleep_time, yield_time = tup
            next_interval = self.to_next_interval(
                timer,
                previous_interval,
                sleep_time=sleep_time,
                yield_time=yield_time,
            )
            intervals.append(next_interval)
            return next_interval

        reduce(on_reduce, values, first_interval)
        return intervals

    async def assert_intervals(
        self, timer: Timer, intervals: list[Interval]
    ) -> None:
        assert await self.consume_timer(timer, limit=len(intervals)) == [
            interval.expected_new_interval for interval in intervals
        ]

    async def consume_timer(self, timer: Timer, limit: int) -> list[float]:
        return [sleep_time async for sleep_time in aslice(timer, 0, limit)]


class test_Timer_1s_half_second_skew(test_Timer):
    skew = 0.5


class test_Timer_30s_five_second_skew(test_Timer):
    interval = 30.0
    skew = 5.0


class test_Timer_30s_five_second_skew_late_epoch(test_Timer):
    epoch = 300000.0
    interval = 30.0
    skew = 5.0
