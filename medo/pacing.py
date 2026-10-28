"""
medo/pacing.py
Adaptive request pacing for the site.

The documented rule is one request at a time with a 1.2-3 s pause.  A fixed
range is wrong in both directions: it is too fast when the site starts
answering 429 (which is exactly when the run must slow down instead of losing
its allowance) and needlessly slow after fifty clean pages.

``AdaptiveDelay`` owns that policy so the scraper and the offline passes share
one behaviour:

* start at the caller's range (1.5-3.0 s by default),
* on a throttle (429 / soft block) double the range, capped at ``MAX_DELAY``,
* after ``SHRINK_AFTER`` clean pages shrink it by 10%, never below
  ``FLOOR_DELAY``.
"""
from __future__ import annotations

import random
import time
from typing import Callable, Optional

FLOOR_DELAY = 1.2
MAX_DELAY = 12.0
SHRINK_AFTER = 40
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
    ) -> None:
        self.low = max(float(low), FLOOR_DELAY)
        self.high = max(float(high), self.low)
        self._clock = clock
        self._rng = rng or random.Random()
        self._on_log = on_log
        self._clean = 0
        self._throttles = 0

    @property
    def range(self) -> tuple[float, float]:
        return self.low, self.high

    def next_delay(self) -> float:
        """A uniformly random pause inside the current range."""
        return self._rng.uniform(self.low, self.high)

    def sleep(self) -> None:
        time.sleep(self.next_delay())

    def throttled(self, retry_after: Optional[float] = None) -> float:
        """Widen the range after a 429 and say how long to wait first."""
        self._throttles += 1
        self._clean = 0
        self.low = min(max(self.low * 2, FLOOR_DELAY), MAX_DELAY)
        self.high = min(max(self.high * 2, self.low), MAX_DELAY)
        wait = self.next_delay()
        if retry_after is not None:
            try:
                header = float(retry_after)
                if header > 0:
                    wait = min(max(header, 5.0), 300.0)
            except (TypeError, ValueError):
                pass
        self._log(f"[pace] site throttled — pausing {wait:.0f}s, then "
                  f"{self.low:.1f}-{self.high:.1f}s per page")
        return wait

    def succeeded(self) -> None:
        """Count one clean page; shrink the range every SHRINK_AFTER of them."""
        self._clean += 1
        if self._clean % LOG_EVERY == 0:
            self._log(f"[pace] {self._clean} clean pages — "
                      f"{self.low:.1f}-{self.high:.1f}s per page")
        if self._clean % SHRINK_AFTER == 0 and self.low > FLOOR_DELAY:
            self.low = max(self.low * 0.9, FLOOR_DELAY)
            self.high = max(self.high * 0.9, self.low)
            self._log(f"[pace] settled to {self.low:.1f}-{self.high:.1f}s "
                      "per page")

    @property
    def throttles(self) -> int:
        return self._throttles

    def _log(self, message: str) -> None:
        if self._on_log:
            try:
                self._on_log(message)
            except Exception:
                pass
