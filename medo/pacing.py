"""
medo/pacing.py
Adaptive request pacing for the site.

The documented rule is one request at a time with a 1-3 s pause.  A fixed
range is wrong in both directions: it is too fast when the site starts
answering 429 (which is exactly when the run must slow down instead of losing
its allowance) and needlessly slow after fifty clean pages.

``AdaptiveDelay`` owns that policy so the scraper and the offline passes share
one behaviour:

* start at the caller's range, or at the range learned in a previous run,
* on a throttle (429 / captcha / quota) double the range, remember the delay
  that failed and raise the learned ceiling to ``last good delay * 1.25``,
* after ``SHRINK_AFTER`` (25) consecutive clean pages shrink the range by 10%,
  never below ``FLOOR_DELAY`` (1.0 s).

The learned ceiling is what makes the next run start carefully after a 429
instead of immediately repeating the pace that failed.  Persistence lives in
``medo.db`` (``save_pace_state`` / ``get_pace_state``); this module has no
database dependency and is unit-tested with a fake clock.
"""
from __future__ import annotations

import random
import time
from typing import Callable, Optional

FLOOR_DELAY = 1.0
MAX_DELAY = 15.0
SHRINK_AFTER = 25
SHRINK_FACTOR = 0.9
CEILING_FACTOR = 1.25
LOG_EVERY = 25


class AdaptiveDelay:
    """A delay range that widens on throttling and settles back when clean."""

    def __init__(
        self,
        low: float = 1.5,
        high: float = 3.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
        on_log: Optional[Callable[[str], None]] = None,
        learned: Optional[tuple[float, float]] = None,
    ) -> None:
        self._clock = clock
        self._rng = rng or random.Random()
        self._on_log = on_log
        self._clean = 0
        self._throttles = 0
        self._throttled = False
        self.last_delay: Optional[float] = None
        self.last_good_delay: Optional[float] = None
        self.failing_delay: Optional[float] = None
        self.learned_ceiling: Optional[float] = None

        start_low = max(float(low), FLOOR_DELAY)
        start_high = max(float(high), start_low)
        if learned and float(learned[0]) > 0:
            # The previous run learned that this pace was safe; start there
            # instead of rediscovering the same 429.
            start_low = max(FLOOR_DELAY, min(float(learned[0]), MAX_DELAY))
            start_high = max(start_low, min(float(learned[1]), MAX_DELAY))
            self.learned_ceiling = start_low
        self.low = start_low
        self.high = start_high

    @property
    def range(self) -> tuple[float, float]:
        return self.low, self.high

    def next_delay(self) -> float:
        """A uniformly random pause inside the current range (jitter kept)."""
        delay = self._rng.uniform(self.low, self.high)
        self.last_delay = delay
        return delay

    def sleep(self) -> None:
        time.sleep(self.next_delay())

    def throttled(self, retry_after: Optional[float] = None) -> float:
        """Widen the range after a 429 and say how long to wait first.

        ``retry_after`` is the header value when the site supplied one (seconds
        or an HTTP date is handled by the caller).  The failure is recorded so
        the log can say which delay was too fast and the learned ceiling never
        starts below that pace again.
        """
        self._throttles += 1
        self._clean = 0
        self._throttled = True
        self.failing_delay = self.last_delay or self.low
        last_good = self.last_good_delay or self.failing_delay
        ceiling = min(max(last_good * CEILING_FACTOR, FLOOR_DELAY), MAX_DELAY)
        self.learned_ceiling = ceiling
        self.low = min(max(self.low * 2, ceiling, FLOOR_DELAY), MAX_DELAY)
        self.high = min(max(self.high * 2, self.low), MAX_DELAY)

        # A 429 is never retried instantly: even without a Retry-After header
        # the first pause is at least five seconds and is shown in the UI.
        wait = max(self.next_delay(), 5.0)
        if retry_after is not None:
            try:
                header = float(retry_after)
                if header > 0:
                    wait = min(max(header, 5.0), 300.0)
            except (TypeError, ValueError):
                pass
        self._log(
            f"[pace] throttle at {self.failing_delay:.1f}s — "
            f"learned ceiling {ceiling:.1f}s, cooling down {wait:.0f}s, "
            f"then {self.low:.1f}-{self.high:.1f}s per page")
        return wait

    def succeeded(self) -> None:
        """Count one clean page; shrink the range every SHRINK_AFTER of them."""
        self._clean += 1
        if self.last_delay is not None:
            self.last_good_delay = self.last_delay
        if self._clean % LOG_EVERY == 0:
            self._log(f"[pace] {self._clean} clean pages — "
                      f"{self.low:.1f}-{self.high:.1f}s per page")
        if self._clean % SHRINK_AFTER == 0 and self.low > FLOOR_DELAY:
            # Never shrink below a pace that already produced a 429.  A
            # merely learned/starting range is not a hard floor; otherwise
            # the pacer could never settle after a quiet start.
            floor = (max(FLOOR_DELAY, self.learned_ceiling or 0.0)
                     if self._throttled else FLOOR_DELAY)
            self.low = max(self.low * SHRINK_FACTOR, floor)
            self.high = max(self.high * SHRINK_FACTOR, self.low)
            self._log(f"[pace] settled to {self.low:.1f}-{self.high:.1f}s "
                      "per page")
        # Never forget a throttle just because the pages became clean again:
        # the ceiling is the floor the next run starts from.
        ceiling = max(self.learned_ceiling or 0.0,
                      (self.last_good_delay or self.low) * CEILING_FACTOR)
        self.learned_ceiling = min(ceiling, MAX_DELAY)

    def cooldown(
        self,
        wait: float,
        *,
        on_tick: Optional[Callable[[float], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> bool:
        """Pause for `wait` seconds with a visible countdown.

        Returns False when stopped during the cool-down.  A 429 must never be
        retried immediately; the caller shows the remaining seconds in the UI
        instead of freezing silently.
        """
        remaining = max(0.0, float(wait))
        while remaining > 0:
            if should_stop and should_stop():
                return False
            if on_tick:
                try:
                    on_tick(remaining)
                except Exception:
                    pass
            step = min(1.0, remaining)
            sleep(step)
            remaining -= step
        return True

    @property
    def throttles(self) -> int:
        return self._throttles

    @property
    def clean_pages(self) -> int:
        return self._clean

    def _log(self, message: str) -> None:
        if self._on_log:
            try:
                self._on_log(message)
            except Exception:
                pass
