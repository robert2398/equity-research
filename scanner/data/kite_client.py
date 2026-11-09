"""Rate-limited, retrying Kite API wrapper.

- Historical-data endpoints are limited to ~3 requests/second: enforced with a
  minimum interval between calls.
- Transient errors (network, HTTP 429/5xx) are retried with exponential backoff.
- Permanent errors (auth token, permissions, bad input) are surfaced immediately
  with clear messages.
"""
from __future__ import annotations

import logging
import random
import time
from functools import wraps
from typing import Callable, TypeVar

from kiteconnect import KiteConnect
from kiteconnect.exceptions import (
    DataException,
    GeneralException,
    InputException,
    NetworkException,
    PermissionException,
    TokenException,
)

from ..errors import AuthError, EntitlementError

log = logging.getLogger("scanner.kite")

T = TypeVar("T")

MIN_CALL_INTERVAL_S = 0.40          # ~2.5 req/s, safely under the 3 req/s limit
MAX_RETRIES = 5
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 45.0


class RateLimitedKite:
    """Wraps a KiteConnect client with rate limiting + retry/backoff."""

    def __init__(self, kite: KiteConnect):
        self._kite = kite
        self._last_call = 0.0
        self.call_count = 0

    def _throttle(self) -> None:
        wait = MIN_CALL_INTERVAL_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        self.call_count += 1

    def _call(self, fn: Callable[[], T], what: str) -> T:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                return fn()
            except TokenException as e:
                raise AuthError(f"Kite session expired/invalid while {what}: {e}. "
                                f"Run `python -m scanner auth login`.") from e
            except PermissionException as e:
                raise EntitlementError(f"Kite API permission denied while {what}: {e}. "
                                       f"Check the historical-data add-on at developers.kite.trade.") from e
            except InputException as e:
                # bad request parameters — retrying will not help
                raise RuntimeError(f"Kite rejected request while {what} (bad input): {e}") from e
            except DataException as e:
                raise RuntimeError(f"Kite data error while {what}: {e}") from e
            except (NetworkException, GeneralException) as e:
                last_exc = e
            except Exception as e:  # timeouts, connection resets
                last_exc = e

            backoff = min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_CAP_S)
            backoff *= 0.75 + random.random() * 0.5   # deterministic outcomes unaffected: jitter only affects timing
            log.warning("Transient error while %s (attempt %d/%d): %s — retrying in %.1fs",
                        what, attempt, MAX_RETRIES, last_exc, backoff)
            time.sleep(backoff)

        raise RuntimeError(f"Giving up on {what} after {MAX_RETRIES} attempts: {last_exc}")

    # -- API surface used by the scanner -------------------------------
    def instruments(self, exchange: str = "NSE"):
        return self._call(lambda: self._kite.instruments(exchange), f"fetching {exchange} instrument master")

    def historical_data(self, instrument_token: int, from_date, to_date, interval: str, continuous: bool = False):
        what = f"fetching {interval} candles for token {instrument_token} ({from_date}..{to_date})"
        return self._call(
            lambda: self._kite.historical_data(instrument_token, from_date, to_date, interval, continuous),
            what,
        )

    def profile(self):
        return self._call(lambda: self._kite.profile(), "fetching profile")
