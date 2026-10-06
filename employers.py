"""Graduate roles straight from employers' own job systems.

Greenhouse, Lever and Ashby publish public, keyless job-board APIs intended for
embedding a company's openings elsewhere. We read the boards below, keep London roles
whose titles look graduate-level, and normalise them to the shared job shape.

To add an employer: find their careers page's hiring platform and board name
(e.g. boards.greenhouse.io/<name>, jobs.lever.co/<name>, jobs.ashbyhq.com/<name>).
"""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

TIMEOUT = 15
CACHE_TTL = 30 * 60

# (platform, board name, display name)
EMPLOYERS = [
    ("greenhouse", "squarepointcapital", "Squarepoint Capital"),
    ("greenhouse", "wehrtyou", "Hudson River Trading"),
    ("greenhouse", "imc", "IMC Trading"),
    ("greenhouse", "towerresearchcapital", "Tower Research Capital"),
    ("greenhouse", "winton", "Winton"),
    ("greenhouse", "point72", "Point72"),
    ("greenhouse", "schonfeld", "Schonfeld"),
    ("greenhouse", "janestreet", "Jane Street"),
    ("greenhouse", "drweng", "DRW"),
    ("greenhouse", "jumptrading", "Jump Trading"),
    ("greenhouse", "quberesearchandtechnologies", "Qube Research & Technologies"),
    ("greenhouse", "stripe", "Stripe"),
    ("greenhouse", "deliveroo", "Deliveroo"),
    ("greenhouse", "monzo", "Monzo"),
    ("greenhouse", "isomorphiclabs", "Isomorphic Labs"),
    ("lever", "palantir", "Palantir"),
    ("lever", "zopa", "Zopa"),
    ("lever", "spotify", "Spotify"),
    ("ashby", "faculty", "Faculty"),
    ("ashby", "wayve", "Wayve"),
    ("ashby", "synthesia", "Synthesia"),
]

GRADUATE = re.compile(
    r"graduate|new grad|\bgrads?\b|early careers?|entry[- ]level|junior|trainee|"
    r"analyst programme|analyst program|\b20(26|27)\b",
    re.I,
)
NOT_GRADUATE = re.compile(
    r"\bintern(ship)?\b|placement|summer|spring week|insight|experienced|"
    r"\bsenior\b|\bsr\.?\b|principal|\bstaff\b|\blead\b|head of|director|\bphds?\b",
    re.I,
)


def is_graduate_role(title: str) -> bool:
    return bool(GRADUATE.search(title)) and not NOT_GRADUATE.search(title)


def is_london(location: str) -> bool:
    return "london" in (location or "").lower()


def _iso_day(value) -> str | None:
    if isinstance(value, (int, float)):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return (value or "")[:10] or None


COUNTRIES = {"united kingdom", "uk", "england", "great britain", "gb", "united states", "usa", "us"}


def tidy_location(location: str) -> str:
    """'London, Montreal, Singapore' -> 'London + 2 other offices'."""
    location = location or ""
    if ";" in location:  # Greenhouse multi-office style: "City, Region, Country; City, Country"
        parts = location.split(";")
    else:  # "London, Montreal, Singapore" or "London, United Kingdom"
        parts = [x for x in location.split(",") if x.strip().lower() not in COUNTRIES]
    parts = [x.strip() for x in parts if x.strip()]
    others = [x for x in parts if not is_london(x)]
    if not others:
        return location.strip() or "London"
    return f"London + {len(others)} other office{'s' if len(others) > 1 else ''}"


def _job(platform, board, employer, jid, title, location, url, posted, description):
    location = tidy_location(location)
    return {
        "id": f"{platform}:{board}:{jid}",
        "source": "employer",
        "employer": employer,
        "title": title.strip(),
        "area": location or "London",
        "salary_min": None,
        "salary_max": None,
        "salary_label": "",
        "salary_annual": None,
        "deadline": None,
        "posted": posted,
        "description": description,
        "url": url,
    }


def fetch_greenhouse(board: str, employer: str, clean) -> list[dict]:
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                     params={"content": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if is_london(loc) and is_graduate_role(j.get("title", "")):
            out.append(_job("greenhouse", board, employer, j["id"], j["title"], loc, j.get("absolute_url"),
                            _iso_day(j.get("first_published") or j.get("updated_at")), clean(j.get("content", ""))))
    return out


def fetch_lever(board: str, employer: str, clean) -> list[dict]:
    r = requests.get(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        loc = (j.get("categories") or {}).get("location", "")
        if is_london(loc) and is_graduate_role(j.get("text", "")):
            out.append(_job("lever", board, employer, j["id"], j["text"], loc, j.get("hostedUrl"),
                            _iso_day(j.get("createdAt")), clean(j.get("descriptionPlain", ""))))
    return out


def fetch_ashby(board: str, employer: str, clean) -> list[dict]:
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = next((l for l in locs if is_london(l)), "")
        if loc and is_graduate_role(j.get("title", "")):
            out.append(_job("ashby", board, employer, j["id"], j["title"], loc, j.get("jobUrl"),
                            _iso_day(j.get("publishedAt")), clean(j.get("descriptionPlain", ""))))
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


class EmployerBoards:
    """Fetches every board in parallel; one cached result shared by all searches."""

    def __init__(self, clean, employers=EMPLOYERS):
        self.clean = clean
        self.employers = employers
        self._lock = threading.Lock()
        self._cached: tuple[float, list[dict], list[str]] | None = None

    def jobs(self, force: bool = False) -> tuple[list[dict], list[str]]:
        with self._lock:
            if self._cached and not force and time.time() - self._cached[0] < CACHE_TTL:
                return self._cached[1], self._cached[2]

        def one(entry):
            platform, board, name = entry
            try:
                return FETCHERS[platform](board, name, self.clean), None
            except Exception as e:  # a dead board shouldn't take the rest down
                log.warning("%s board %s failed: %s", platform, board, e)
                return [], name

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(one, self.employers))
        jobs = [j for js, _ in results for j in js]
        failed = [name for _, name in results if name]
        with self._lock:
            self._cached = (time.time(), jobs, failed)
        return jobs, failed
