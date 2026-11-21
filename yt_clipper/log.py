"""Tiny logger. Listeners are per-thread so concurrent web jobs keep separate logs."""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable

_local = threading.local()


def set_listener(fn: Callable[[str], None] | None) -> None:
    _local.listener = fn


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)
    fn = getattr(_local, "listener", None)
    if fn:
        try:
            fn(msg)
        except Exception:
            pass
