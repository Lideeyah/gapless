"""Execution session from Pyth's live market-hours flag, with the hand-maintained calendar as the fallback.

The keyless Pyth feed list carries, per US equity feed, `market_hours.is_open` and the exchange's trading
schedule including this year's holidays and early closes, e.g.
  America/New_York;0930-1600,0930-1600,0930-1600,0930-1600,0930-1600,C,C;0907/C,1127/0930-1300,1225/C,...
That is the calendar market_calendar.py was maintaining by hand, published by the exchange's data vendor
and read live. This module derives the execution session from it and only ever tightens the recorder's
label: a live reading can turn `open` into `overnight` or `weekend`, never the other way. If the flag is
unreadable the hand calendar decides, exactly as before. Pyth here can only ever make the keeper more
careful, never less.

The recorder's session_state() is untouched: its labels are evidence and stay simple.
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pyth  # noqa: E402
from market_calendar import execution_session  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
STRICTNESS = {"open": 0, "overnight": 1, "weekend": 2}


def parse_schedule(schedule):
    """-> (weekly: list of 7 'HHMM-HHMM' | 'C' Mon..Sun, overrides: {'MMDD': 'HHMM-HHMM' | 'C'})."""
    parts = schedule.split(";")
    weekly = parts[1].split(",") if len(parts) > 1 else []
    overrides = {}
    if len(parts) > 2 and parts[2]:
        for item in parts[2].split(","):
            if "/" in item:
                day, hours = item.split("/", 1)
                overrides[day] = hours
    return weekly, overrides


def live_session(now_utc: datetime, labelled: str, mh: dict) -> str:
    """Session implied by one market_hours reading, before combining with the label."""
    if mh["is_open"]:
        return "open"
    et = now_utc.astimezone(EASTERN)
    weekly, overrides = parse_schedule(mh.get("schedule", ""))
    today = overrides.get(et.strftime("%m%d"), weekly[et.weekday()] if len(weekly) == 7 else None)
    if et.weekday() >= 5 or today == "C":
        return "weekend"  # a full closure day, whether a Saturday or a holiday: the most defensive regime
    return "overnight"  # outside session hours on a trading day, including a half-day afternoon


def execution_session_live(now_utc: datetime, labelled: str, fetch=pyth.market_hours):
    """-> (session, source, note). source is 'pyth' when the live flag decided, 'calendar' when it fell back."""
    fallback = execution_session(now_utc, labelled)
    try:
        mh = fetch()
    except Exception as exc:  # noqa: BLE001
        return fallback, "calendar", f"market_hours unreadable ({exc}); hand calendar used"
    live = live_session(now_utc, labelled, mh)
    session = max((labelled, live), key=STRICTNESS.get)  # only ever tighten the recorder's label
    note = f"pyth is_open={mh['is_open']}"
    if session != fallback:
        note += f"; hand calendar says {fallback}, live schedule says {live}: live wins"
    return session, "pyth", note
