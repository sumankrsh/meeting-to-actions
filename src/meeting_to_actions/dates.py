"""Turn due-date phrases ("by Friday", "end of month", "12 December") into dates.

Every relative phrase is resolved against the meeting date. The rules are
deliberately simple and listed in the README:

- a bare weekday ("Friday", "by Thursday") is the first such day after the meeting
- "next <weekday>" is that weekday in the following calendar week (weeks start Monday)
- "end of (the) week" / "EOW" is the Friday of the meeting's week (the next Friday if
  the meeting is at the weekend)
- "next week" is the Monday of the following week
- "end of (the) month" / "EOM" is the last day of the meeting's month
- "in N days/weeks" counts from the meeting date
- a day and month without a year is the next such date on or after the meeting
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "a": 1, "a couple of": 2}

_WD = "|".join(WEEKDAYS)
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUM = r"\d{1,2}|one|two|three|four|five|six|a couple of|a"

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("iso", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    ("day_month", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MON})\.?(?: (\d{{4}}))?\b", re.I)),
    ("month_day", re.compile(rf"\b({_MON})\.? (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (\d{{4}}))?\b", re.I)),
    (
        "eod",
        re.compile(
            r"\b(today|tonight|this (?:morning|afternoon|evening)|later today|end of (?:the )?day|eod|close of play|cop)\b",
            re.I,
        ),
    ),
    ("tomorrow", re.compile(r"\b(tomorrow)\b", re.I)),
    ("next_weekday", re.compile(rf"\bnext ({_WD})\b", re.I)),
    ("this_weekday", re.compile(rf"\b(?:this |on |by |before |until |till )?({_WD})\b", re.I)),
    ("eow", re.compile(r"\b(end of (?:the |this )?week|eow)\b", re.I)),
    ("next_week", re.compile(r"\b(next week)\b", re.I)),
    ("eom", re.compile(r"\b(end of (?:the |this )?month|eom|month[- ]end)\b", re.I)),
    ("in_n", re.compile(rf"\b(?:in|within) ({_NUM}) (day|week)s?\b", re.I)),
]


def _next_weekday(after: dt.date, weekday: int) -> dt.date:
    days = (weekday - after.weekday()) % 7 or 7
    return after + dt.timedelta(days=days)


def _roll_year(day: int, month: int, year: int | None, ref: dt.date | None) -> dt.date | None:
    try:
        if year:
            return dt.date(year, month, day)
        if ref is None:
            return None
        d = dt.date(ref.year, month, day)
        return d if d >= ref else dt.date(ref.year + 1, month, day)
    except ValueError:
        return None


def resolve(kind: str, m: re.Match, ref: dt.date | None) -> dt.date | None:
    if kind == "iso":
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if kind == "day_month":
        return _roll_year(int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3) or 0) or None, ref)
    if kind == "month_day":
        return _roll_year(int(m.group(2)), MONTHS[m.group(1).lower()], int(m.group(3) or 0) or None, ref)
    if ref is None:
        return None
    if kind == "eod":
        return ref
    if kind == "tomorrow":
        return ref + dt.timedelta(days=1)
    if kind == "next_weekday":
        monday_next = ref + dt.timedelta(days=7 - ref.weekday())
        return monday_next + dt.timedelta(days=WEEKDAYS.index(m.group(1).lower()))
    if kind == "this_weekday":
        return _next_weekday(ref, WEEKDAYS.index(m.group(1).lower()))
    if kind == "eow":
        if ref.weekday() <= 4:
            return ref + dt.timedelta(days=4 - ref.weekday())
        return _next_weekday(ref, 4)
    if kind == "next_week":
        return ref + dt.timedelta(days=7 - ref.weekday())
    if kind == "eom":
        return dt.date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
    if kind == "in_n":
        raw = m.group(1).lower()
        n = int(raw) if raw.isdigit() else NUMBERS[raw]
        unit = 7 if m.group(2).lower() == "week" else 1
        return ref + dt.timedelta(days=n * unit)
    return None


def find_due(text: str, ref: dt.date | None) -> tuple[dt.date | None, str]:
    """Return (date, phrase) for the first due-date phrase in `text`.

    The phrase is returned even when it can't be resolved (no meeting date), so
    the report can still show it.
    """
    best: tuple[int, str, re.Match] | None = None
    for kind, pattern in PATTERNS:
        m = pattern.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), kind, m)
    if best is None:
        return None, ""
    _, kind, m = best
    return resolve(kind, m, ref), m.group(0).strip()
