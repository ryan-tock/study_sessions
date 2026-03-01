"""
Course catalog scraper for catalog.mines.edu.

Fetches department HTML pages concurrently and caches them to disk.
Cache location: data/{academic_year}_{season_letter}/coursesaz_website/{dept}.txt
Pending location: data/{academic_year}_{season_letter}/coursesaz_website_pending/{dept}.txt

Two phases:
1. fetch_courses()  — download pages from web, save to pending dir, parse
2. promote_pending_cache() — move pending dir to final dir on import confirm
3. load_courses_from_cache() — parse already-saved files (no network)
"""
import asyncio
import html as html_mod
import os
import re
import shutil
import ssl
import urllib.request

import certifi

CATALOG_BASE = "https://catalog.mines.edu/coursesaz/"
SEASON_LETTER = {"spring": "A", "fall": "B"}

_DEPT_LINK_PAT = re.compile(r'<a href="/coursesaz/(\w+)/">')
_TITLE_DEPT_PAT = re.compile(r"\(([A-Z]{2,8})\)")
_STRONG_PAT = re.compile(r"<strong>(.*?)</strong>", re.DOTALL)


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _cache_dir(data_dir: str, academic_year: int, season: str, pending: bool = False) -> str:
    letter = SEASON_LETTER.get(season, "?")
    subdir = "coursesaz_website_pending" if pending else "coursesaz_website"
    return os.path.join(data_dir, f"{academic_year}_{letter}", subdir)


def cache_exists(data_dir: str, academic_year: int, season: str) -> bool:
    """True if final (confirmed) course data exists for this term."""
    d = _cache_dir(data_dir, academic_year, season)
    return os.path.isdir(d) and any(f.endswith(".txt") for f in os.listdir(d))


def pending_cache_exists(data_dir: str, academic_year: int, season: str) -> bool:
    """True if pending (preview, not yet confirmed) course data exists for this term."""
    d = _cache_dir(data_dir, academic_year, season, pending=True)
    return os.path.isdir(d) and any(f.endswith(".txt") for f in os.listdir(d))


def wipe_cache(data_dir: str, academic_year: int, season: str) -> int:
    """Delete all .txt files in the final course cache dir for a term. Returns count deleted."""
    d = _cache_dir(data_dir, academic_year, season)
    if not os.path.isdir(d):
        return 0
    count = 0
    for fname in os.listdir(d):
        if fname.endswith(".txt"):
            os.remove(os.path.join(d, fname))
            count += 1
    return count


def wipe_pending_cache(data_dir: str, academic_year: int, season: str) -> None:
    """Delete the pending cache dir entirely."""
    d = _cache_dir(data_dir, academic_year, season, pending=True)
    if os.path.isdir(d):
        shutil.rmtree(d)


def promote_pending_cache(data_dir: str, academic_year: int, season: str) -> None:
    """Move the pending cache to the final cache dir, replacing any existing final cache."""
    pending_d = _cache_dir(data_dir, academic_year, season, pending=True)
    final_d = _cache_dir(data_dir, academic_year, season, pending=False)
    if not os.path.isdir(pending_d):
        return
    if os.path.isdir(final_d):
        shutil.rmtree(final_d)
    os.rename(pending_d, final_d)


def parse_dept_html(html: str) -> list[dict]:
    """Parse one department page and return a list of course dicts."""
    title_m = re.search(r"<title>(.*?)</title>", html)
    if not title_m:
        return []
    dept_m = _TITLE_DEPT_PAT.search(title_m.group(1))
    if not dept_m:
        return []
    dept_code = dept_m.group(1)

    courses = []
    seen: set[tuple] = set()
    for sm in _STRONG_PAT.finditer(html):
        text = sm.group(1).strip()
        parts = [p.strip() for p in text.split(". ")]
        if len(parts) < 3:
            continue
        code_str = parts[0]
        if not code_str.startswith(dept_code):
            continue
        identifier = code_str[len(dept_code):]
        if not identifier or not identifier[0].isdigit():
            continue
        title = html_mod.unescape(parts[1])
        hours = parts[2].split()[0] if parts[2] else ""
        key = (dept_code, identifier)
        if key not in seen:
            seen.add(key)
            courses.append({
                "department": dept_code,
                "identifier": identifier,
                "title": title,
                "semester_hours": hours,
            })
    return courses


def load_courses_from_cache(data_dir: str, academic_year: int, season: str,
                            pending: bool = False) -> list[dict]:
    """Load and parse all saved department HTML files. No network access."""
    d = _cache_dir(data_dir, academic_year, season, pending=pending)
    if not os.path.isdir(d):
        return []
    courses = []
    for fname in sorted(os.listdir(d)):
        if fname.endswith(".txt"):
            with open(os.path.join(d, fname), "r", encoding="utf-8", errors="replace") as f:
                courses.extend(parse_dept_html(f.read()))
    return courses


async def _fetch_one(slug: str, url: str, cache_path: str) -> tuple[str, str | None, str | None]:
    """Fetch one dept page from the web and save it. Returns (slug, html, error)."""
    try:
        html = await asyncio.to_thread(_get, url)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(html)
        return slug, html, None
    except Exception as e:
        return slug, None, str(e)


async def fetch_courses(
    data_dir: str, academic_year: int, season: str
) -> tuple[list[dict], list[str]]:
    """
    Fetch all course data fresh from the web, saving to the pending dir.
    Always fetches from web (never reads existing files). Returns (courses, fetch_errors).
    Call promote_pending_cache() after a successful import to commit the data.
    """
    d = _cache_dir(data_dir, academic_year, season, pending=True)
    # Wipe pending dir to ensure a clean fetch
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)

    try:
        index_html = await asyncio.to_thread(_get, CATALOG_BASE)
    except Exception as e:
        raise RuntimeError(f"Failed to reach catalog site: {e}")

    slugs = _DEPT_LINK_PAT.findall(index_html)
    if not slugs:
        raise RuntimeError(
            "No departments found on catalog page — page structure may have changed"
        )

    tasks = [
        _fetch_one(slug, CATALOG_BASE + slug + "/", os.path.join(d, slug + ".txt"))
        for slug in slugs
    ]
    results = await asyncio.gather(*tasks)

    courses: list[dict] = []
    errors: list[str] = []
    for slug, html, err in results:
        if err:
            errors.append(f"{slug}: {err}")
        elif html:
            courses.extend(parse_dept_html(html))

    return courses, errors
