"""
PDF parsers for exam schedule documents.

Supports two PDF formats:
  - Common Hour Exam Schedule: lines of "Month DD, YYYY  DEPT123/456"
  - Final Exam Schedule (detailed grid): day-code headers + course rows, dates scattered throughout
"""
import io
import re
from datetime import datetime

from pypdf import PdfReader

_MONTHS = (
    "January|February|March|April|May|June|"
    "July|August|September|October|November|December"
)
_DATE_PAT = re.compile(
    rf"({_MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})", re.IGNORECASE
)
_WEEKDAY_MAP = {
    "mon": 0, "tues": 1, "tue": 1,
    "wed": 2, "thurs": 3, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


def _parse_date(text: str) -> datetime | None:
    m = _DATE_PAT.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}, {m.group(3)}", "%B %d, %Y")
    except ValueError:
        return None


def parse_common_hour_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Parse a Common Hour Exam Schedule PDF.

    Each relevant line looks like:
        March 5, 2026   MATH123/456
        April 2, 2026   CSCI101

    Returns a list of dicts: {department, identifier, date (ISO string)}.
    Duplicate (dept, identifier, date) entries are deduplicated.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    results = []
    seen: set[tuple] = set()

    # Pattern: one or more alpha chars (dept) followed by digits (identifier)
    # Optionally more identifiers separated by "/"
    _course_pat = re.compile(r"([A-Za-z]+)\s*(\d+(?:/\d+)*)")

    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue

        for line in text.splitlines():
            date = _parse_date(line)
            if not date:
                continue

            # Everything after the date string
            after = _DATE_PAT.sub("", line, count=1).strip()
            match = _course_pat.search(after)
            if not match:
                continue

            dept = match.group(1).upper()
            identifiers = match.group(2).split("/")

            for ident in identifiers:
                ident = ident.strip()
                if not ident:
                    continue
                key = (dept, ident, date.date().isoformat())
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "department": dept,
                        "identifier": ident,
                        "date": date.date().isoformat(),
                    })

    return results


def parse_finals_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Parse a Final Exam Schedule (detailed grid) PDF.

    The format contains:
      - Date lines: "December 15, 2025"
      - Day-block headers: "MON - 8:00 AM - 10:00 AM" (or TUES, WED, THURS, FRI)
      - Course rows: "12 MATH 201 3 credits" or similar

    Strategy: collect all dates from the PDF, then for each course map its
    day-of-week code to the corresponding exam date.

    Returns a list of dicts: {department, identifier, date (ISO string)}.
    Duplicate courses are deduplicated (first occurrence wins).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    dates: list[datetime] = []
    working_day: str = ""
    # (dept, identifier) -> day_str (e.g. "MON")
    finals: dict[tuple, str] = {}

    # Day-block header: "MON - 8:00 AM - 10:00 AM" or "THURS - 1:00 PM - 3:00 PM"
    _day_header = re.compile(
        r"\b(\w{3,5})\s*-\s*\d{1,2}:\d{2}\s*[APap][Mm]\s*-\s*\d{1,2}:\d{2}\s*[APap][Mm]",
        re.IGNORECASE,
    )
    # Course row: a number, then DEPT, then a number (identifier)
    # e.g. "12 MATH 201 3 credits" or "5 CSCI 261A 4"
    _course_row = re.compile(r"\b\d+\s+([A-Za-z]{2,6})\s+(\d+)\w*\b")

    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue

        for line in text.splitlines():
            # Collect exam-week dates
            dt = _parse_date(line)
            if dt:
                dates.append(dt)
                continue

            # Check for day-block header
            day_match = _day_header.search(line)
            if day_match:
                working_day = day_match.group(1)
                continue

            # Check for course row
            course_match = _course_row.search(line)
            if course_match and working_day:
                dept = course_match.group(1).upper()
                ident = course_match.group(2)
                key = (dept, ident)
                if key not in finals:
                    finals[key] = working_day

    # Map each course's day code to an actual date
    results = []
    for (dept, ident), day_str in finals.items():
        weekday = _WEEKDAY_MAP.get(day_str.lower())
        if weekday is None:
            continue
        matching = [d for d in dates if d.weekday() == weekday]
        if not matching:
            continue
        exam_date = min(matching).date()
        results.append({
            "department": dept,
            "identifier": ident,
            "date": exam_date.isoformat(),
        })

    return results
