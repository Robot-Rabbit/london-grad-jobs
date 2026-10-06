"""Live London graduate job feed: Reed + Adzuna, merged with the curated scheme snapshot.

Every job is normalised to one dict shape (see curated.curated_jobs) plus a `sector`.
"""

import html
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import requests

from curated import curated_jobs

log = logging.getLogger(__name__)

REED_SEARCH = "https://www.reed.co.uk/api/1.0/search"
REED_DETAIL = "https://www.reed.co.uk/api/1.0/jobs/{id}"
ADZUNA_SEARCH = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
TIMEOUT = 10
CACHE_TTL = 15 * 60

# Order matters: first match wins.
SECTOR_RULES = [
    ("Education", r"\b(teach|teaching assistant|tutor|school|sen\b|learning support|pgce)"),
    ("Law", r"\b(law|legal|solicitor|paralegal|trainee associate|training contract)\b"),
    ("Cyber security", r"\b(cyber|security engineer|infosec|forensic)"),
    ("Data & AI", r"\b(data|analytics|machine learning|\bai\b|scientist|insight)"),
    ("Technology", r"\b(software|developer|engineer(ing)? graduate|technology|devops|it support|cloud|product manager|\bit\b)"),
    ("Investment banking", r"\b(investment bank|capital markets|m&a|advisory analyst|financial advisory|quant\b|quantitative)"),
    ("Insurance & actuarial", r"\b(actuar|insurance|underwrit|broking|claims)"),
    ("Audit & accounting", r"\b(audit|accountan|aca\b|acca|cima|bookkeep)"),
    ("Tax", r"\b(tax)\b"),
    ("Sales & business", r"\b(sales|business development|account manager|recruit|commercial)"),
    ("Consulting", r"\b(consult|strategy|advisory)"),
    ("Finance", r"\b(financ|analyst|banking|risk|compliance|credit|treasury|investment)"),
    ("Construction & property", r"\b(surveyor|site manager|construction|property|estate agent|lettings|planning)"),
    ("Engineering", r"\b(civil|mechanical|electrical|structural|engineer)"),
    ("Marketing & media", r"\b(marketing|pr\b|communications|media|content|social media|brand)"),
    ("Public sector & charity", r"\b(nhs|civil service|council|charity|policy|social work|care worker)"),
    ("Operations & project management", r"\b(project manager|operations|management trainee|logistics|supply chain|coordinator)"),
]


def classify(title: str, extra: str = "") -> str:
    # The title is the strongest signal; descriptions are full of recruiter boilerplate.
    for text in (title.lower(), extra.lower()):
        for sector, pattern in SECTOR_RULES:
            if re.search(pattern, text):
                return sector
    return "Other"


def clean_text(s: str, limit: int = 4000) -> str:
    s = html.unescape(s or "")  # some boards send entity-escaped HTML
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _period(value) -> str:
    """Job boards mix annual, daily and hourly pay in the same field."""
    if value <= 60:
        return "hour"
    if value <= 1500:
        return "day"
    return "year"


ANNUALISE = {"hour": 37.5 * 52, "day": 230, "year": 1}


def salary_info(lo, hi) -> tuple[str, float | None]:
    """Returns (display label, annual equivalent for sorting)."""
    lo, hi = lo or hi, hi or lo
    if not lo:
        return "", None
    period = _period(hi)
    money = (lambda v: f"£{v:,.2f}".replace(".00", "")) if period == "hour" else (lambda v: f"£{int(v):,}")
    label = money(lo) if int(hi * 100) == int(lo * 100) else f"{money(lo)}–{money(hi)}"
    if period != "year":
        label += f" per {period}"
    return label, round(hi * ANNUALISE[period])


def salary_label(lo, hi) -> str:
    return salary_info(lo, hi)[0]


_POSTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$", re.I)


def tidy_area(s: str) -> str:
    """Reed sometimes gives a bare postcode such as 'SW1P2DY'."""
    s = (s or "").strip()
    m = _POSTCODE.match(s)
    return f"{m.group(1).upper()} {m.group(2).upper()}" if m else (s or "London")


def _uk_date(s):
    """Reed dates are dd/mm/yyyy."""
    try:
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Reed
REED_PAGE = 100
REED_MAX = 400


def _reed_page(api_key: str, q: str, skip: int) -> dict:
    params = {
        "keywords": q or "graduate",
        "locationName": "London",
        "distanceFromLocation": 15,
        "graduate": "true",
        "resultsToTake": REED_PAGE,
        "resultsToSkip": skip,
    }
    r = requests.get(REED_SEARCH, params=params, auth=(api_key, ""), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_reed(api_key: str, q: str) -> list[dict]:
    first = _reed_page(api_key, q, 0)
    results = list(first.get("results", []))
    total = min(int(first.get("totalResults") or 0), REED_MAX)
    skips = range(REED_PAGE, total, REED_PAGE)
    if skips:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for page in pool.map(lambda sk: _reed_page(api_key, q, sk), skips):
                results.extend(page.get("results", []))
    out = []
    for j in results:
        lo, hi = j.get("minimumSalary"), j.get("maximumSalary")
        title = j.get("jobTitle") or ""
        desc = clean_text(j.get("jobDescription", ""))
        out.append({
            "id": f"reed:{j.get('jobId')}",
            "source": "reed",
            "employer": j.get("employerName") or "Employer not named",
            "title": title,
            "area": tidy_area(j.get("locationName")),
            "salary_min": lo,
            "salary_max": hi,
            "salary_label": salary_info(lo, hi)[0],
            "salary_annual": salary_info(lo, hi)[1],
            "deadline": _uk_date(j.get("expirationDate")),
            "posted": _uk_date(j.get("date")),
            "description": desc,
            "url": j.get("jobUrl") or f"https://www.reed.co.uk/jobs/{j.get('jobId')}",
            "sector": classify(title, desc[:300]),
        })
    return out


def fetch_reed_detail(api_key: str, job_id: str) -> str:
    r = requests.get(REED_DETAIL.format(id=job_id), auth=(api_key, ""), timeout=TIMEOUT)
    r.raise_for_status()
    return clean_text(r.json().get("jobDescription", ""), limit=12000)


# ---------------------------------------------------------------- Adzuna
def fetch_adzuna(app_id: str, app_key: str, q: str, pages: int = 5) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what": f"graduate {q}".strip(),
            "where": "London",
            "distance": 25,  # km
            "max_days_old": 45,
            "results_per_page": 50,
            "sort_by": "date",
            "content-type": "application/json",
        }
        r = requests.get(ADZUNA_SEARCH.format(page=page), params=params, timeout=TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        for j in results:
            lo, hi = j.get("salary_min"), j.get("salary_max")
            title = clean_text(j.get("title", ""))
            desc = clean_text(j.get("description", ""))
            category = (j.get("category") or {}).get("label", "")
            posted = (j.get("created") or "")[:10] or None
            out.append({
                "id": f"adzuna:{j.get('id')}",
                "source": "adzuna",
                "employer": (j.get("company") or {}).get("display_name") or "Employer not named",
                "title": title,
                "area": (j.get("location") or {}).get("display_name") or "London",
                "salary_min": lo,
                "salary_max": hi,
                "salary_label": salary_info(lo, hi)[0]
                + (" (Adzuna estimate)" if lo and str(j.get("salary_is_predicted")) == "1" else ""),
                "salary_annual": salary_info(lo, hi)[1],
                "deadline": None,
                "posted": posted,
                "description": desc,
                "url": j.get("redirect_url"),
                "sector": classify(title, f"{category} {desc[:300]}"),
            })
        if len(results) < 50:
            break
    return out


# ---------------------------------------------------------------- merge
def _key(job):
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return norm(job["employer"]), norm(job["title"])


def _matches(job, q: str) -> bool:
    terms = q.lower().split()
    hay = f"{job['employer']} {job['title']} {job['area']} {job.get('sector', '')} {job['description']}".lower()
    return all(t in hay for t in terms)


class JobFeed:
    def __init__(self, reed_key=None, adzuna_id=None, adzuna_key=None, boards=None):
        """`boards` is an employers.EmployerBoards; None leaves employer boards out."""
        self.boards = boards
        self.reed_key = reed_key
        self.adzuna_id = adzuna_id
        self.adzuna_key = adzuna_key
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._by_id: dict[str, dict] = {}

    @property
    def live_sources(self) -> list[str]:
        s = ["employers"] if self.boards else []
        if self.reed_key:
            s.append("reed")
        if self.adzuna_id and self.adzuna_key:
            s.append("adzuna")
        return s

    def search(self, q: str = "", force: bool = False) -> dict:
        q = (q or "").strip()[:100]
        ck = q.lower()
        with self._lock:
            hit = self._cache.get(ck)
        if hit and not force and time.time() - hit[0] < CACHE_TTL:
            return hit[1]

        tasks, errors = {}, []
        with ThreadPoolExecutor(max_workers=3) as pool:
            boards_fut = pool.submit(self.boards.jobs, force) if self.boards else None
            if self.reed_key:
                tasks["Reed"] = pool.submit(fetch_reed, self.reed_key, q)
            if self.adzuna_id and self.adzuna_key:
                tasks["Adzuna"] = pool.submit(fetch_adzuna, self.adzuna_id, self.adzuna_key, q)
            live = []
            for name, fut in tasks.items():
                try:
                    live.extend(fut.result())
                except Exception as e:  # one feed failing shouldn't sink the other
                    log.warning("%s feed failed: %s", name, e)
                    errors.append(f"{name} is not responding right now")

            employer_jobs = []
            if boards_fut:
                employer_jobs, failed = boards_fut.result()
                employer_jobs = [dict(j, sector=classify(j["title"], j["description"][:300])) for j in employer_jobs]
                if failed:
                    errors.append(f"{len(failed)} employer job boards are not responding right now")

        schemes = [dict(j, sector=classify(j["title"]), salary_annual=j["salary_min"]) for j in curated_jobs()]
        if q:
            schemes = [j for j in schemes if _matches(j, q)]
            employer_jobs = [j for j in employer_jobs if _matches(j, q)]

        merged, seen = [], set()
        # Schemes first so their deadlines win on duplicates, then employers' own listings.
        for job in schemes + employer_jobs + live:
            k = _key(job)
            if k in seen:
                continue
            seen.add(k)
            merged.append(job)

        today = date.today().isoformat()
        merged = [j for j in merged if not j["deadline"] or j["deadline"] >= today]

        result = {
            "jobs": merged,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "live_sources": self.live_sources,
            "errors": errors,
        }
        with self._lock:
            self._cache[ck] = (time.time(), result)
            for j in merged:
                self._by_id[j["id"]] = j
        return result

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._by_id.get(job_id)

    def full_description(self, job: dict) -> str:
        if job["source"] == "reed" and self.reed_key:
            try:
                return fetch_reed_detail(self.reed_key, job["id"].split(":", 1)[1])
            except Exception as e:
                log.warning("Reed detail failed: %s", e)
        return job["description"]
