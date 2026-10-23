"""Timing tolerance shared by tests that assert lower bounds on elapsed time."""

import time

# asyncio computes timer deadlines from its loop clock (time.monotonic) and may fire them up to one
# tick early, so a sleep, timeout or rate slot can end up to two ticks before the requested time.
# That is ~31 ms on Windows with Python 3.11/3.12 (15.6 ms GetTickCount64 ticks) and negligible
# elsewhere. The extra 2 ms covers scheduling noise.
EARLY_WAKE_S = 2 * time.get_clock_info("monotonic").resolution + 0.002
EARLY_WAKE_MS = EARLY_WAKE_S * 1000
