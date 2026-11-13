"""Pure alarm logic: parsing the end time and deciding whether to notify."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from border import Summary, fmt_td

# Notify when the queue is LONG: more than MAX_CARS cars OR wait >= MAX_WAIT.
MAX_CARS = 30
MAX_WAIT = timedelta(hours=1)

INTERVALS_MIN = (5, 10, 15, 30, 60)


def parse_until(text: str, now: datetime) -> datetime | None:
    """'04:00', '4', '4.30', '0400' -> next occurrence of that time (today or tomorrow)."""
    text = text.strip()
    m = (re.fullmatch(r"(\d{1,2})(?:[:.\s](\d{2}))?", text)
         or re.fullmatch(r"(\d{2})(\d{2})", text))
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mnt > 59:
        return None
    target = now.replace(hour=h, minute=mnt, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def hour_choices(now: datetime, count: int = 12) -> list[str]:
    first = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return [f"{(first + timedelta(hours=i)):%H:%M}" for i in range(count)]


def alarm_reasons(s: Summary, max_cars: int = MAX_CARS, max_wait: timedelta = MAX_WAIT) -> list[str]:
    reasons = []
    if s.in_queue > max_cars:
        reasons.append(f"{s.in_queue} cars in queue (> {max_cars})")
    if s.estimate is not None and s.estimate >= max_wait:
        reasons.append(f"wait ~{fmt_td(s.estimate)} (≥ {fmt_td(max_wait)})")
    return reasons
