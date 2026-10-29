"""tests/test_pacing.py — the adaptive delay and its learned ceiling.

No network and no real sleeping: a fake clock and a fake rate-limited server
exercise the exact policy the harvester uses.
"""
from __future__ import annotations

import random

from medo.db import get_connection, get_pace_state, init_db, save_pace_state
from medo.pacing import AdaptiveDelay, FLOOR_DELAY, SHRINK_AFTER


def test_floor_is_one_second_and_jitter_stays_inside_the_range():
    pace = AdaptiveDelay(1.5, 3.0, rng=random.Random(7))
    for _ in range(50):
        delay = pace.next_delay()
        assert 1.5 <= delay <= 3.0
    assert FLOOR_DELAY == 1.0


def test_twenty_five_clean_pages_shrink_by_ten_percent_not_below_floor():
    pace = AdaptiveDelay(2.0, 3.0, rng=random.Random(1))
    for _ in range(SHRINK_AFTER):
        pace.next_delay()
        pace.succeeded()
    assert 1.8 <= pace.low < 2.0
    assert pace.high < 3.0

    very_slow = AdaptiveDelay(1.0, 1.2)
    for _ in range(SHRINK_AFTER * 20):
        very_slow.next_delay()
        very_slow.succeeded()
    assert very_slow.low == FLOOR_DELAY


def test_throttle_doubles_and_records_the_learned_ceiling():
    pace = AdaptiveDelay(1.0, 1.2, rng=random.Random(2))
    pace.next_delay()
    pace.succeeded()                 # last good delay is known
    good = pace.last_good_delay
    wait = pace.throttled()
    assert wait >= 5.0
    assert pace.failing_delay == good
    assert pace.low >= 2.0
    assert pace.high >= pace.low
    assert pace.learned_ceiling >= good * 1.25 - 1e-9


def test_next_run_starts_from_the_learned_range():
    pace = AdaptiveDelay(1.5, 3.0, rng=random.Random(3),
                         learned=(2.6, 4.0))
    assert pace.low == 2.6 and pace.high == 4.0
    assert pace.range == (2.6, 4.0)


def test_clean_pages_do_not_shrink_back_below_a_learned_ceiling():
    pace = AdaptiveDelay(1.0, 1.2, rng=random.Random(4))
    pace.next_delay()
    pace.throttled()                 # ceiling raised
    ceiling = pace.learned_ceiling
    for _ in range(SHRINK_AFTER * 4):
        pace.next_delay()
        pace.succeeded()
    assert pace.low >= ceiling


def test_cooldown_counts_down_and_can_be_stopped():
    ticks: list[float] = []
    slept: list[float] = []
    pace = AdaptiveDelay(1.0, 1.2)
    assert pace.cooldown(3.4, on_tick=ticks.append, sleep=slept.append)
    assert sum(slept) >= 3.4
    assert ticks and ticks[0] > ticks[-1]
    assert not pace.cooldown(10, should_stop=lambda: True,
                             sleep=slept.append)


def test_learned_state_round_trips_through_the_database(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()
    assert get_pace_state(db) == {}
    save_pace_state(db, 3.2, 4.8, ceiling=4.0)
    state = get_pace_state(db)
    assert state["low"] == 3.2 and state["high"] == 4.8
    assert state["ceiling"] == 4.0


class _FakeRateLimitedServer:
    """One request at a time; rejects anything faster than `min_interval`."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.now = 0.0
        self.last_request = None
        self.requests = 0
        self.throttles = 0

    def request(self) -> int:
        self.requests += 1
        if self.last_request is not None and \
                self.now - self.last_request < self.min_interval:
            self.throttles += 1
            return 429
        self.last_request = self.now
        return 200


def test_pacing_converges_below_a_server_that_limits_at_two_seconds():
    """A fake server rejects < 2 s; the pacer must learn, not loop on 429."""
    server = _FakeRateLimitedServer(min_interval=2.0)
    pace = AdaptiveDelay(1.0, 1.2, rng=random.Random(11))
    statuses: list[int] = []
    for _ in range(120):
        delay = pace.next_delay()
        server.now += delay
        status = server.request()
        statuses.append(status)
        if status == 429:
            wait = pace.throttled()
            assert wait >= 5.0          # a cool-down, never an instant retry
            server.now += wait
        else:
            pace.succeeded()

    # The first attempt may fail, but the pacer must not keep failing forever.
    assert server.throttles <= 3
    assert statuses[-20:].count(200) == 20
    assert pace.low >= 2.0
