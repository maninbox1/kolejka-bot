from datetime import datetime, timedelta

from border import MINSK, parse_date, render, summarise

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=MINSK)


def car(reg, status=2, changed=None):
    return {"regnum": "X", "registration_date": reg, "changed_date": changed or reg, "status": status}


QUEUE = {
    "carLiveQueue": [
        car("10:00:00 12.11.2026", status=3, changed="11:50:00 12.11.2026"),  # waited 1h50
        car("09:00:00 12.11.2026", status=3, changed="11:00:00 12.11.2026"),  # waited 2h
        car("10:30:00 12.11.2026"),
        car("11:00:00 12.11.2026"),
        car("11:30:00 12.11.2026"),
        car("11:45:00 12.11.2026"),
        car("11:55:00 12.11.2026", status=9),
    ],
    "carPriority": [car("11:40:00 12.11.2026")],
}
STATS = {"carLastHour": 2, "carLastDay": 48}


def test_parse_both_formats():
    assert parse_date("10:30:00 12.11.2026") == datetime(2026, 11, 12, 10, 30, tzinfo=MINSK)
    assert parse_date("12.11.2026 10:30:00") == datetime(2026, 11, 12, 10, 30, tzinfo=MINSK)
    assert parse_date("") is None


def test_summary():
    s = summarise("Брузги", QUEUE, STATS, now=NOW)
    assert s.in_queue == 4
    assert s.priority_waiting == 1
    assert s.sent_last_hour == 2 and s.sent_last_day == 48
    assert s.first_registered == datetime(2026, 11, 12, 10, 30, tzinfo=MINSK)
    assert s.last_registered == datetime(2026, 11, 12, 11, 45, tzinfo=MINSK)
    assert s.last_called_at == datetime(2026, 11, 12, 11, 50, tzinfo=MINSK)
    assert s.recent_actual_wait == timedelta(minutes=115)
    # 5 cars ahead / 2 cars per hour = 2.5h
    assert s.estimate == timedelta(hours=2.5)
    assert s.estimate_low == timedelta(hours=1.5)   # front car has waited 1h30
    assert s.estimate_high == timedelta(hours=2.5)


def test_empty_data_does_not_crash():
    s = summarise("Брузги", {}, {}, now=NOW)
    assert s.in_queue == 0 and s.estimate is None
    assert "Not enough data" in render(s)


def test_render():
    text = render(summarise("Брузги", QUEUE, STATS, now=NOW))
    assert "In queue now: <b>4</b> (+1 priority)" in text
    assert "~2h 30m" in text
