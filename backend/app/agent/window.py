"""Relative-date parsing. 'last 4 months', 'this summer', 'in 2025', 'the 15 Oct reading'."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple


def _months_before(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def parse_last_n(text: str) -> Optional[Tuple[str, int]]:
    m = re.search(r"last\s+(\d+)\s+(day|week|month|year)s?", text, re.I)
    if not m:
        return None
    unit = m.group(2).lower() + "s"
    return unit, int(m.group(1))


def parse_window(text: str, today: date) -> Dict[str, Any]:
    t = text.lower()
    out: Dict[str, Any] = {"start": None, "end": today, "label": ""}

    m = parse_last_n(t)
    if m:
        unit, n = m
        if unit == "days":
            start = today - timedelta(days=n)
        elif unit == "weeks":
            start = today - timedelta(weeks=n)
        elif unit == "months":
            start = _months_before(today, n)
        else:
            start = _months_before(today, n * 12)
        out.update({"start": start, "end": today, "label": f"last {n} {unit}"})
        return out

    m = re.search(r"\bin\s+(\d{4})\b", t)
    if m:
        y = int(m.group(1))
        out.update({"start": date(y, 1, 1), "end": date(y, 12, 31), "label": str(y)})
        return out

    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        y = int(m.group(1))
        out.update({"start": date(y, 1, 1), "end": date(y, 12, 31), "label": str(y)})
        return out

    if "this year" in t or "this calendar year" in t or "so far this year" in t:
        out.update({"start": date(today.year, 1, 1), "end": today, "label": f"{today.year} to date"})
        return out

    if "this summer" in t:
        out.update({"start": date(today.year, 6, 1), "end": date(today.year, 8, 31),
                    "label": f"summer {today.year}"})
        return out

    if "yesterday" in t:
        d = today - timedelta(days=1)
        out.update({"start": d, "end": d, "label": "yesterday"})
        return out

    if "today" in t or "right now" in t:
        out.update({"start": None, "end": today, "label": "now"})
        return out

    return out


MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS_FULL = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update(MONTHS_FULL)


def parse_date_phrase(text: str, today: date) -> Optional[Dict[str, Any]]:
    """Find a short date like '15 Oct' / '24 Sep 2026' and return a one-day window."""
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\b(?:\s+(\d{4}))?", text)
    if not m:
        return None
    day = int(m.group(1))
    mon = MONTHS.get(m.group(2).lower())
    if not mon:
        return None
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        d = date(year, mon, day)
    except ValueError:
        return None
    return {"start": d, "end": d, "label": d.isoformat(), "single": d}
