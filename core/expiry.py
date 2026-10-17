"""
Fire-extinguisher dates: parse what Qwen read off a tag / label and decide the status.

Qwen answers one line per date it can read (see prompts.EXTINGUISHER_PROMPT):
    DATE <date as written> | <words printed next to it>     → kind from the words
or  NONE <no_tag|too_small|blurry|not_extinguisher>
(the older "DATE <KIND> <date>" form is accepted too).

Dates with only a month (03/2027, MAR 2027) or a year (2027) count until the END of
that month / year — a tag saying "next service 03/2027" is still valid on 31 March.

Status (most important first):
    expired         due date has passed
    expiring_soon   due within `soon_days`
    valid           due later
    unclear         dates were read but none says what it means (UNKNOWN only)
    unreadable      no date could be read (reason: too_small / blurry / no_tag …)
"""

import calendar
import re
from datetime import date

KINDS = ("EXPIRY", "NEXT_SERVICE", "LAST_SERVICE", "MANUFACTURED", "UNKNOWN")
NONE_REASONS = ("no_tag", "too_small", "blurry", "not_extinguisher")
STATUS_TEXT = {"reading": "READING", "valid": "VALID", "expiring_soon": "DUE SOON",
               "expired": "EXPIRED", "unclear": "CHECK DATE", "unreadable": "UNREADABLE"}

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})
MONTHS["sept"] = 9


def _year(y: str) -> int:
    """2- or 4-digit year → 4 digits; anything else (or outside 1990-2100) is not a date."""
    if len(y) not in (2, 4):
        raise ValueError(f"not a year: {y}")
    y = int(y) + (2000 if len(y) == 2 else 0)
    if not 1990 <= y <= 2100:
        raise ValueError(f"implausible year: {y}")
    return y


def _end(y: int, m: int = 12, d: int = None) -> date:
    return date(y, m, d or calendar.monthrange(y, m)[1])


def parse_date(text: str, dayfirst: bool = True):
    """
    "15/03/2027", "2027-03-15", "03/2027", "3/27", "MAR 2027", "15 March 2027", "2027"
    → (last day the date covers, precision "day" | "month" | "year"), or None.
    Two numbers with no 4-digit year ("03/27") are month/year — the usual tag style.
    """
    t = text.strip().lower().replace(",", " ").replace(".", "/")
    try:
        # month names: "15 mar 2027", "mar 2027", "march 15 2027", "mar-27"
        m = re.search(r"(?:(\d{1,2})\s*[-/ ]?\s*)?([a-z]{3,9})\s*[-/ ]?\s*(\d{1,2}\s+)?(\d{4}|\d{2})\b", t)
        if m and m.group(2) in MONTHS:
            mon, y = MONTHS[m.group(2)], _year(m.group(4))
            day = m.group(1) or (m.group(3) or "").strip()
            return (_end(y, mon, int(day)), "day") if day else (_end(y, mon), "month")
        nums = re.findall(r"\d+", t)
        if len(nums) >= 3:
            a, b, c = nums[:3]
            if len(a) == 4:                                  # 2027-03-15
                return _end(_year(a), int(b), int(c)), "day"
            d, mo = (a, b) if dayfirst else (b, a)
            if int(mo) > 12 and int(d) <= 12:                # the other order is the only valid one
                d, mo = mo, d
            return _end(_year(c), int(mo), int(d)), "day"
        if len(nums) == 2:
            a, b = nums
            if len(a) == 4:                                  # 2027-03
                return _end(_year(a), int(b)), "month"
            return _end(_year(b), int(a)), "month"           # 03/2027, 3/27
        if len(nums) == 1 and len(nums[0]) == 4:
            return _end(_year(nums[0])), "year"
    except ValueError:                                       # month 13, day 32, …
        return None
    return None


# words next to a date → what the date means (checked in this order: "next
# recommended servicing" must not count as a service date)
KIND_WORDS = [
    ("NEXT_SERVICE", r"next|due|recommended|re-?inspect|valid (?:until|till)"),
    ("EXPIRY", r"expir|\bexp\b|use (?:by|before)|replace"),
    ("MANUFACTURED", r"manufact|\bmfg|\bmfd|\bmade\b|year of"),
    ("LAST_SERVICE", r"servic|inspect|has been|refill|recharg|check|tested|maint|\bon\b|date"),
]


def kind_from_words(words: str) -> str:
    w = (words or "").lower()
    return next((kind for kind, rx in KIND_WORDS if re.search(rx, w)), "UNKNOWN")


def parse_answer(text: str, dayfirst: bool = True) -> dict:
    """Qwen's answer → {"dates": [{kind, raw, words, date, precision}], "none": reason | None}."""
    dates, none, seen = [], None, set()
    for line in (text or "").splitlines():
        line = line.strip().strip("`*- ")
        m = re.match(r"(?:DATE\s+)?([^|]+?)\s*\|\s*(.*)$", line, re.I)     # DATE <date> | <words>
        old = re.match(r"DATE\s+(" + "|".join(KINDS) + r")\s+(.+)$", line, re.I)
        if old:                                                          # DATE <KIND> <date>
            kind, raw, words = old.group(1).upper(), old.group(2).strip(), ""
        elif m:
            raw, words = m.group(1).strip(), m.group(2).strip()
            if re.match(r"none\b", words, re.I):                 # "2022 | NONE too_small"
                words = ""
            kind = kind_from_words(words)
        else:
            raw = None
        if raw:
            parsed = parse_date(raw, dayfirst)
            if parsed and (kind, parsed[0]) not in seen:      # small models repeat lines
                seen.add((kind, parsed[0]))
                dates.append({"kind": kind, "raw": raw, "words": words,
                              "date": parsed[0], "precision": parsed[1]})
            continue
        m = re.match(r"NONE\b\W*([a-z_]*)", line, re.I)
        if m:
            reason = m.group(1).lower()
            none = reason if reason in NONE_REASONS else "unreadable"
    if dates:
        none = None
    elif none is None:
        none = "unreadable"
    return {"dates": dates, "none": none}


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def due_date(dates: list, service_months: int = 12, life_years: int = 10, today: date = None):
    """
    The date the extinguisher stops being OK, and why:
      EXPIRY / NEXT_SERVICE      → that date (the latest one, if several)
      LAST_SERVICE (latest)      → + service_months
      MANUFACTURED               → + life_years (end of life; the earlier limit wins)
    A "last service" date after `today` cannot be one — the words were attached to the
    wrong date — so it counts as the next-service date.
    → (due date, basis text, precision) or (None, None, None) when only UNKNOWN dates
    were read.
    """
    if today:
        dates = [{**d, "kind": "NEXT_SERVICE"} if d["kind"] == "LAST_SERVICE" and d["date"] > today
                 else d for d in dates]

    def latest(kind):
        ds = [d for d in dates if d["kind"] == kind]
        return max(ds, key=lambda d: d["date"]) if ds else None

    candidates = []
    if latest("EXPIRY"):
        d = latest("EXPIRY")
        candidates.append((d["date"], "expiry date", d["precision"]))
    if latest("NEXT_SERVICE"):
        d = latest("NEXT_SERVICE")
        candidates.append((d["date"], "next service", d["precision"]))
    elif latest("LAST_SERVICE"):
        d = latest("LAST_SERVICE")
        candidates.append((_add_months(d["date"], service_months),
                           f"last service + {service_months} months", d["precision"]))
    if latest("MANUFACTURED"):
        d = latest("MANUFACTURED")
        candidates.append((_add_months(d["date"], 12 * life_years),
                           f"manufactured + {life_years} years", d["precision"]))
    if not candidates:
        return None, None, None
    return min(candidates, key=lambda c: c[0])


def status_for(due, today: date, soon_days: int = 30) -> str:
    if due is None:
        return "unclear"
    if due < today:
        return "expired"
    if (due - today).days <= soon_days:
        return "expiring_soon"
    return "valid"


def assess(dates: list, today: date, service_months: int = 12, life_years: int = 10,
           soon_days: int = 30) -> dict:
    """Dates from one read → {"status", "due", "basis", "precision"}."""
    due, basis, precision = due_date(dates, service_months, life_years, today)
    return {"status": status_for(due, today, soon_days), "due": due, "basis": basis,
            "precision": precision}


def fmt_date(d: date, precision: str = "day") -> str:
    if d is None:
        return "-"
    return {"day": d.strftime("%d/%m/%Y"), "month": d.strftime("%m/%Y"),
            "year": d.strftime("%Y")}[precision]
