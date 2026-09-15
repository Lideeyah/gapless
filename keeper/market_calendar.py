"""NYSE full-day closures and early (13:00 ET) closes, 2026 and 2027, as published by the NYSE.

The recorder's session_state() deliberately has no calendar (its labels are evidence, kept simple).
The keeper layers this on top for execution only: a weekday holiday is treated as `weekend`
(the most defensive regime) and the afternoon of a half-day as `overnight`. The CSV keeps labelling
those hours `open`; the divergence is documented in the README.
"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

HOLIDAYS = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18),
    date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}
EARLY_CLOSES = {date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}  # market closes 13:00 ET
EARLY_CLOSE_AT = time(13, 0)


def execution_session(now_utc: datetime, labelled: str) -> str:
    """Apply the calendar to the recorder's label for execution purposes only."""
    if labelled != "open":
        return labelled
    et = now_utc.astimezone(EASTERN)
    if et.date() in HOLIDAYS:
        return "weekend"
    if et.date() in EARLY_CLOSES and et.time() >= EARLY_CLOSE_AT:
        return "overnight"
    return labelled
