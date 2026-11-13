"""Fetching and summarising queue data from mon.declarant.by (belarusborder.by backend)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


MINSK = ZoneInfo("Europe/Minsk")

CHECKPOINTS = {
    "bruzgi": {"id": "3b797d4d-706a-440f-a1a4-826c191e1e36", "name": "Брузги"},
    "berestovitsa": {"id": "7e46a2d1-ab2f-11ec-bafb-ac1f6bf889c1", "name": "Берестовица"},
}

STATUS_WAITING = 2   # "Прибыл в ЗО" — in the waiting zone
STATUS_CALLED = 3    # "Вызван в ПП" — called to the checkpoint
STATUS_CANCELLED = 9

_DATE_FORMATS = ("%H:%M:%S %d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%H:%M %d.%m.%Y", "%d.%m.%Y %H:%M")


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=MINSK)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=MINSK)
    except ValueError:
        return None


@dataclass
class Summary:
    name: str
    now: datetime
    in_queue: int                      # cars waiting in the live queue now
    priority_waiting: int              # cars waiting in the priority queue now
    sent_last_hour: int
    sent_last_day: int
    first_registered: datetime | None  # oldest car still waiting
    last_registered: datetime | None   # newest car in the queue
    last_called_at: datetime | None    # when the most recent car was called to the checkpoint
    recent_actual_wait: timedelta | None  # median wait of the last few called cars
    estimate: timedelta | None         # how long you'd wait if you registered now
    estimate_low: timedelta | None
    estimate_high: timedelta | None


def _status(v: dict) -> int | None:
    try:
        return int(v.get("status"))
    except (TypeError, ValueError):
        return None


def summarise(name: str, queue: dict, stats: dict, vehicle: str = "car",
              now: datetime | None = None) -> Summary:
    now = now or datetime.now(MINSK)
    live = queue.get(f"{vehicle}LiveQueue") or []
    prio = queue.get(f"{vehicle}Priority") or []

    waiting = [v for v in live if _status(v) == STATUS_WAITING]
    prio_waiting = [v for v in prio if _status(v) == STATUS_WAITING]

    reg_waiting = [d for d in (parse_date(v.get("registration_date")) for v in waiting) if d]
    reg_all = [d for d in (parse_date(v.get("registration_date")) for v in live
                           if _status(v) != STATUS_CANCELLED) if d]

    called = []
    for v in live:
        if _status(v) != STATUS_CALLED:
            continue
        reg, chg = parse_date(v.get("registration_date")), parse_date(v.get("changed_date"))
        if reg and chg and chg >= reg:
            called.append((chg, chg - reg))
    called.sort(key=lambda x: x[0], reverse=True)
    recent = [w for _, w in called[:5]]
    recent_actual_wait = timedelta(seconds=statistics.median(w.total_seconds() for w in recent)) \
        if recent else None

    hour = int(stats.get(f"{vehicle}LastHour") or 0)
    day = int(stats.get(f"{vehicle}LastDay") or 0)

    # Throughput (cars/hour). Blend the last hour with the 24h average so one slow/fast hour
    # doesn't dominate.
    rates = [r for r in (hour, day / 24) if r > 0]
    ahead = len(waiting) + len(prio_waiting)
    candidates: list[timedelta] = []
    if rates:
        candidates.append(timedelta(hours=ahead / (sum(rates) / len(rates))))
    if reg_waiting:
        # The car at the front has been waiting this long; a newcomer waits at least about as long
        # if the queue keeps its current length.
        candidates.append(now - min(reg_waiting))
    if recent_actual_wait:
        candidates.append(recent_actual_wait)

    estimate = low = high = None
    if candidates:
        estimate = candidates[0] if rates else timedelta(
            seconds=statistics.median(c.total_seconds() for c in candidates))
        low, high = min(candidates), max(candidates)

    return Summary(
        name=name,
        now=now,
        in_queue=len(waiting),
        priority_waiting=len(prio_waiting),
        sent_last_hour=hour,
        sent_last_day=day,
        first_registered=min(reg_waiting) if reg_waiting else None,
        last_registered=max(reg_all) if reg_all else None,
        last_called_at=called[0][0] if called else None,
        recent_actual_wait=recent_actual_wait,
        estimate=estimate,
        estimate_low=low,
        estimate_high=high,
    )


# ---------- formatting ----------

def fmt_td(td: timedelta | None) -> str:
    if td is None:
        return "n/a"
    minutes = int(td.total_seconds() // 60)
    d, rem = divmod(minutes, 24 * 60)
    h, m = divmod(rem, 60)
    parts = ([f"{d}d"] if d else []) + ([f"{h}h"] if h or d else []) + [f"{m}m"]
    return " ".join(parts)


def fmt_dt(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "n/a"
    return f"{dt:%d.%m %H:%M} ({fmt_td(now - dt)} ago)"


def render(s: Summary) -> str:
    lines = [
        f"🚗 <b>{s.name}</b> — passenger cars",
        f"<i>as of {s.now:%d.%m.%Y %H:%M} (Minsk)</i>",
        "",
        f"In queue now: <b>{s.in_queue}</b>"
        + (f" (+{s.priority_waiting} priority)" if s.priority_waiting else ""),
        f"Sent to checkpoint, last hour: <b>{s.sent_last_hour}</b>",
        f"Sent to checkpoint, last 24h: <b>{s.sent_last_day}</b>",
        "",
        f"First car in queue registered: {fmt_dt(s.first_registered, s.now)}",
        f"Last car registered: {fmt_dt(s.last_registered, s.now)}",
    ]
    if s.last_called_at:
        lines.append(f"Last car called to checkpoint: {fmt_dt(s.last_called_at, s.now)}")
    if s.recent_actual_wait:
        lines.append(f"Recently called cars waited: ~{fmt_td(s.recent_actual_wait)}")
    lines.append("")
    if s.estimate is None:
        lines.append("⏳ Not enough data to estimate the wait.")
    else:
        eta = s.now + s.estimate
        lines.append(f"⏳ If you register now: <b>~{fmt_td(s.estimate)}</b>")
        if s.estimate_low != s.estimate_high:
            lines.append(f"   range {fmt_td(s.estimate_low)} – {fmt_td(s.estimate_high)}")
        lines.append(f"   expected call ≈ {eta:%d.%m %H:%M}")
    return "\n".join(lines)
