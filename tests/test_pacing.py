"""tests/test_pacing.py — the adaptive delay's policy.

A fixed range is wrong in both directions: too fast when the site answers 429,
needlessly slow after fifty clean pages.  These are the exact rules from
PROMPT.md (phase 4.4), tested without sleeping.
"""
import random

from medo.pacing import FLOOR_DELAY, MAX_DELAY, SHRINK_AFTER, AdaptiveDelay


def _controller(low=1.5, high=3.0):
    return AdaptiveDelay(low, high, rng=random.Random(3))


def test_starts_at_the_requested_range_and_never_below_the_floor():
    pace = AdaptiveDelay(0.4, 0.8, rng=random.Random(1))
    assert pace.range == (FLOOR_DELAY, FLOOR_DELAY)
    assert all(FLOOR_DELAY <= pace.next_delay() <= FLOOR_DELAY for _ in range(20))


def test_a_throttle_doubles_the_range_up_to_the_cap():
    pace = _controller()
    pace.throttled()
    assert pace.range == (3.0, 6.0)
    pace.throttled()
    assert pace.range == (6.0, 12.0)
    pace.throttled()
    assert pace.range == (MAX_DELAY, MAX_DELAY)


def test_a_throttle_returns_the_wait_and_honours_retry_after():
    pace = _controller()
    wait = pace.throttled()
    assert 3.0 <= wait <= 6.0                 # first wait uses the new range
    assert pace.throttled(retry_after="45") == 45.0
    assert pace.throttled(retry_after="9999") == 300.0   # never hours
    assert pace.throttles == 3


def test_clean_pages_shrink_the_range_but_stop_at_the_floor():
    pace = _controller()
    for _ in range(SHRINK_AFTER):
        pace.succeeded()
    low, _ = pace.range
    assert low == round(1.5 * 0.9, 1) or abs(low - 1.35) < 1e-9
    for _ in range(SHRINK_AFTER * 40):
        pace.succeeded()
    assert pace.range[0] == FLOOR_DELAY


def test_clean_pages_before_the_threshold_change_nothing():
    pace = _controller()
    for _ in range(SHRINK_AFTER - 1):
        pace.succeeded()
    assert pace.range == (1.5, 3.0)
