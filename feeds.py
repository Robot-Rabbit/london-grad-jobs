"""Live London graduate job feed: Reed + Adzuna, merged with the curated scheme snapshot.

Every job is normalised to one dict shape (see curated.curated_jobs) plus a `sector`.
"""

import hashlib
import html
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import requests

from curated import curated_jobs

log = logging.getLogger(__name__)

REED_SEARCH = "https://www.reed.co.uk/api/1.0/search"
REED_DETAIL = "https://www.reed.co.uk/api/1.0/jobs/{id}"
ADZUNA_SEARCH = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
JOOBLE_SEARCH = "https://jooble.org/api/{key}"
CAREERJET_SEARCH = "https://search.api.careerjet.net/v4/query"
TIMEOUT = 10
CACHE_TTL = 15 * 60
BOARDS_MIN_AGE = 15 * 60

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


# ---------------------------------------------------------------- level
# Roles for people starting out: graduate jobs and schemes, plus entry-level roles that
# need no experience. Senior roles that slip through job boards' filters are dropped.
GRADUATE_TITLE = re.compile(r"graduate|\bgrads?\b|new grad|\b20(26|27)\b|analyst programme|analyst program", re.I)
ENTRY_TITLE = re.compile(
    r"\bjunior\b|\bjnr\b|trainee|entry[- ]level|\bapprentice\b|\bassistant\b|\bintern(ship)?\b|"
    r"school leaver|first job|no experience|early careers?|early talent|\bassociate\b|\btrainee\b",
    re.I,
)
ENTRY_TEXT = re.compile(
    r"no (previous |prior |formal )?experience (is )?(needed|required|necessary)|"
    r"full training (will be |is )?(given|provided)|entry[- ]level|first step in your career|"
    r"perfect for (a )?(recent )?graduates?|ideal for (school leavers|graduates|someone starting)",
    re.I,
)
SENIOR_TITLE = re.compile(
    r"\bsenior\b|\bsnr\b|\bsr\.?\b|principal|\bstaff (engineer|scientist|developer)|head of|director|"
    r"\bchief\b|\bvp\b|vice president|\blead\b(?!\s*gen)|experienced|\bexpert\b|\bconsultant (psychiatrist|surgeon)",
    re.I,
)
MANAGER_TITLE = re.compile(r"\bmanager\b", re.I)
MANAGER_OK = re.compile(r"trainee|graduate|junior|assistant", re.I)
YEARS_EXPERIENCE = re.compile(
    r"(?:minimum (?:of )?|at least )?(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?[\u2019']?\s+(?:of\s+)?"
    r"(?:relevant\s+|proven\s+|professional\s+|commercial\s+|industry\s+|demonstrable\s+|post[- ]qualification\s+)?"
    r"(?:experience|pqe)|(\d{1,2})\s*\+?\s*years?\s*pqe",
    re.I,
)


# Training courses advertised as jobs ("traineeships", often with fees) aren't real vacancies.
COURSE_AD = re.compile(
    r"\btraineeships?\b|course fees?|enrol(?:l)?ment fee|tuition fee|funded course|"
    r"pay (?:for|towards) (?:the|your) (?:course|training)|training provider",
    re.I,
)


def is_course_ad(title: str, description: str = "") -> bool:
    return bool(COURSE_AD.search(f"{title} {description or ''}"))


def too_senior(title: str, description: str = "") -> bool:
    """True for roles aimed at experienced hires rather than people starting out."""
    if SENIOR_TITLE.search(title):
        return True
    if MANAGER_TITLE.search(title) and not MANAGER_OK.search(title):
        return True
    # Asking for 2+ years' experience, or any post-qualification experience (PQE), means it isn't a starter role.
    for m in YEARS_EXPERIENCE.finditer(description or ""):
        years = int(m.group(1) or m.group(2))
        if years >= 2 or "pqe" in m.group(0).lower():
            return True
    return False


def level_of(title: str, description: str = "", default: str | None = None) -> str | None:
    """'graduate', 'entry', or `default` when neither the title nor text says."""
    if GRADUATE_TITLE.search(title):
        return "graduate"
    if ENTRY_TITLE.search(title) or ENTRY_TEXT.search(description or ""):
        return "entry"
    return default


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


ANNUALISE = {"hour": 37.5 * 52, "day": 230, "week": 52, "month": 12, "year": 1}


def salary_info(lo, hi, period: str | None = None) -> tuple[str, float | None]:
    """Returns (display label, annual equivalent for sorting).

    `period` ("year", "month", "week", "day", "hour") when the source states it;
    otherwise it is guessed from the size of the number."""
    lo, hi = lo or hi, hi or lo
    if not lo:
        return "", None
    lo, hi = float(lo), float(hi)
    period = period or _period(hi)
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


REED_ENTRY_KEYWORDS = ("entry level", "trainee", "junior", "no experience")


def _reed_page(api_key: str, keywords: str, skip: int, graduate: bool = True) -> dict:
    params = {
        "keywords": keywords,
        "locationName": "London",
        "distanceFromLocation": 15,
        "resultsToTake": REED_PAGE,
        "resultsToSkip": skip,
    }
    if graduate:
        params["graduate"] = "true"
    r = requests.get(REED_SEARCH, params=params, auth=(api_key, ""), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_reed(api_key: str, q: str) -> list[dict]:
    """Reed's graduate-flagged jobs (up to REED_MAX), plus one page each of entry-level
    searches so roles for people with no experience show up too."""
    first = _reed_page(api_key, q or "graduate", 0)
    results = [(j, "graduate") for j in first.get("results", [])]
    total = min(int(first.get("totalResults") or 0), REED_MAX)
    with ThreadPoolExecutor(max_workers=6) as pool:
        grad_pages = pool.map(lambda sk: _reed_page(api_key, q or "graduate", sk), range(REED_PAGE, total, REED_PAGE))
        entry_pages = pool.map(lambda kw: _reed_page(api_key, f"{kw} {q}".strip(), 0, graduate=False),
                               REED_ENTRY_KEYWORDS)
        for page in grad_pages:
            results += [(j, "graduate") for j in page.get("results", [])]
        for page in entry_pages:
            results += [(j, None) for j in page.get("results", [])]
    out, seen = [], set()
    for j, flagged in results:
        if j.get("jobId") in seen:
            continue
        seen.add(j.get("jobId"))
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
            "applicants": j.get("applications"),
            "description": desc,
            "url": j.get("jobUrl") or f"https://www.reed.co.uk/jobs/{j.get('jobId')}",
            "sector": classify(title, desc[:300]),
            # Reed's graduate flag counts as a graduate signal when the title says nothing.
            "level": level_of(title, desc, flagged),
        })
    return out


def fetch_reed_detail(api_key: str, job_id: str) -> str:
    r = requests.get(REED_DETAIL.format(id=job_id), auth=(api_key, ""), timeout=TIMEOUT)
    r.raise_for_status()
    return clean_text(r.json().get("jobDescription", ""), limit=12000)


# ---------------------------------------------------------------- Adzuna
def _adzuna_page(app_id: str, app_key: str, q: str, page: int) -> list[dict]:
    params = {
        "app_id": app_id,
        "app_key": app_key,
        # Any of these words, plus the visitor's own search terms if they gave any.
        "what_or": "graduate trainee junior apprentice entry",
        **({"what": q} if q else {}),
        "where": "London",
        "distance": 25,  # km
        "max_days_old": 45,
        "results_per_page": 50,
        "sort_by": "date",
        "content-type": "application/json",
    }
    r = requests.get(ADZUNA_SEARCH.format(page=page), params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("results", [])


ADZUNA_PAGES = 3
ADZUNA_TTL = 30 * 60  # Adzuna's free plan allows ~25 requests a minute and 250 a day


def fetch_adzuna(app_id: str, app_key: str, q: str, pages: int = ADZUNA_PAGES) -> list[dict]:
    # Pages are fetched at the same time (one after another took ~15s). A page that's
    # refused (e.g. rate-limited) is skipped; only if every page fails does Adzuna count as down.
    def safe(n):
        try:
            return _adzuna_page(app_id, app_key, q, n)
        except requests.RequestException as e:
            log.warning("Adzuna page %s failed: %s", n, e)
            return None

    with ThreadPoolExecutor(max_workers=pages) as pool:
        page_results = [p for p in pool.map(safe, range(1, pages + 1)) if p is not None]
    if not page_results:
        raise RuntimeError("every Adzuna page failed")
    out = []
    for results in page_results:
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
                "level": level_of(title, desc, "graduate" if "graduate" in category.lower() else None),
            })
    return out


# ---------------------------------------------------------------- Jooble
def parse_salary_text(text: str) -> tuple[float | None, float | None]:
    """'£28k - £32k', '28000 - 32000 GBP', '£12.50 per hour' -> (low, high)."""
    nums = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", (text or "").lower()):
        v = float(m.group(1).replace(",", ""))
        nums.append(v * 1000 if m.group(2) else v)
    nums = [n for n in nums if n >= 5]  # drop stray small numbers
    if not nums:
        return None, None
    return min(nums), max(nums)


def _any_date(value) -> str | None:
    """ISO ('2026-10-05T00:00:00.0000000') or RFC 2822 ('Mon, 05 Oct 2026 ...') -> YYYY-MM-DD."""
    if not value:
        return None
    value = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return None


def fetch_jooble(api_key: str, q: str, pages: int = 3) -> list[dict]:
    return _jooble_pass(api_key, f"graduate {q}".strip(), pages) + _jooble_pass(api_key, f"entry level {q}".strip(), 2)


def _jooble_pass(api_key: str, keywords: str, pages: int) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        body = {"keywords": keywords, "location": "London", "radius": "25",
                "page": str(page), "ResultOnPage": "50"}
        r = requests.post(JOOBLE_SEARCH.format(key=api_key), json=body, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        for j in jobs:
            title = clean_text(j.get("title", ""))
            desc = clean_text(j.get("snippet", ""))
            lo, hi = parse_salary_text(j.get("salary", ""))
            label, annual = salary_info(lo, hi)
            out.append({
                "id": f"jooble:{j.get('id')}",
                "source": "jooble",
                "employer": clean_text(j.get("company", "")) or "Employer not named",
                "title": title,
                "area": clean_text(j.get("location", "")) or "London",
                "salary_min": lo, "salary_max": hi,
                "salary_label": label, "salary_annual": annual,
                "deadline": None,
                # Jooble only gives the last-updated date, not the first-posted one.
                "posted": _any_date(j.get("updated")),
                "posted_kind": "updated",
                "applicants": None,
                "description": desc,
                "url": j.get("link"),
                "sector": classify(title, desc[:300]),
                "level": level_of(title, desc, "graduate" if keywords.startswith("graduate") else None),
            })
        if len(jobs) < 50:
            break
    return out


# ---------------------------------------------------------------- Careerjet
CAREERJET_PERIOD = {"Y": "year", "M": "month", "W": "week", "D": "day", "H": "hour"}


def fetch_careerjet(api_key: str, q: str, user_ip: str, user_agent: str, pages: int = 3) -> list[dict]:
    return (_careerjet_pass(api_key, f"graduate {q}".strip(), user_ip, user_agent, pages)
            + _careerjet_pass(api_key, f"entry level {q}".strip(), user_ip, user_agent, 2))


def _careerjet_pass(api_key: str, keywords: str, user_ip: str, user_agent: str, pages: int) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        params = {
            "locale_code": "en_GB",
            "keywords": keywords,
            "location": "London",
            "radius": 25,
            "page": page,
            "page_size": 100,
            "sort": "date",
            # Careerjet asks for the IP and browser of the person searching.
            "user_ip": user_ip or "127.0.0.1",
            "user_agent": user_agent or "london-grad-jobs",
        }
        r = requests.get(CAREERJET_SEARCH, params=params, auth=(api_key, ""), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("jobs", []) if data.get("type", "JOBS") == "JOBS" else []
        for j in jobs:
            title = clean_text(j.get("title", ""))
            desc = clean_text(j.get("description", ""))
            label, annual = salary_info(j.get("salary_min"), j.get("salary_max"),
                                        CAREERJET_PERIOD.get(j.get("salary_type")))
            if not label and j.get("salary"):
                label = clean_text(j["salary"])
            url = j.get("url") or ""
            out.append({
                # Stable across processes, unlike hash(): ids must match between server instances.
                "id": "careerjet:" + hashlib.sha1(url.encode()).hexdigest()[:12],
                "source": "careerjet",
                "employer": clean_text(j.get("company", "")) or "Employer not named",
                "title": title,
                "area": clean_text(j.get("locations", "")) or "London",
                "salary_min": j.get("salary_min"), "salary_max": j.get("salary_max"),
                "salary_label": label, "salary_annual": annual,
                "deadline": None,
                "posted": _any_date(j.get("date")),
                "applicants": None,
                "description": desc,
                "url": url,
                "sector": classify(title, desc[:300]),
                "level": level_of(title, desc, "graduate" if keywords.startswith("graduate") else None),
            })
        if page >= int(data.get("pages") or 1):
            break
    return out


# ---------------------------------------------------------------- merge
def _key(job):
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return norm(job["employer"]), norm(job["title"])


_TITLE_NOISE = re.compile(r"\b(20\d\d|full[- ]time|london|uk|united kingdom|programme|program|analyst|graduate)\b", re.I)


def _core_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _TITLE_NOISE.sub(" ", title.lower()))


def same_role(a: dict, b: dict) -> bool:
    """Same employer and one title's core words contained in the other's."""
    if re.sub(r"[^a-z]", "", a["employer"].lower())[:6] != re.sub(r"[^a-z]", "", b["employer"].lower())[:6]:
        return False
    ca, cb = _core_title(a["title"]), _core_title(b["title"])
    return len(min(ca, cb, key=len)) >= 8 and (ca in cb or cb in ca)


def _matches(job, q: str) -> bool:
    terms = q.lower().split()
    hay = f"{job['employer']} {job['title']} {job['area']} {job.get('sector', '')} {job['description']}".lower()
    return all(t in hay for t in terms)


class JobFeed:
    def __init__(self, reed_key=None, adzuna_id=None, adzuna_key=None, boards=None,
                 jooble_key=None, careerjet_key=None):
        """`boards` is an employers.EmployerBoards; None leaves employer boards out."""
        self.boards = boards
        self.jooble_key = jooble_key
        self.careerjet_key = careerjet_key
        self.reed_key = reed_key
        self.adzuna_id = adzuna_id
        self.adzuna_key = adzuna_key
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self._by_id: dict[str, dict] = {}
        self._adzuna_cache: dict[str, tuple[float, list[dict]]] = {}

    @property
    def live_sources(self) -> list[str]:
        s = ["employers"] if self.boards else []
        if self.reed_key:
            s.append("reed")
        if self.adzuna_id and self.adzuna_key:
            s.append("adzuna")
        if self.careerjet_key:
            s.append("careerjet")
        if self.jooble_key:
            s.append("jooble")
        return s

    def search(self, q: str = "", force: bool = False, user_ip: str = "", user_agent: str = "",
               max_age: float = CACHE_TTL) -> dict:
        """`max_age` (seconds) is how old a cached result may be before we fetch again."""
        q = (q or "").strip()[:100]
        ck = q.lower()
        with self._lock:
            hit = self._cache.get(ck)
        if hit and not force and time.time() - hit[0] < max_age:
            return hit[1]

        tasks, errors = {}, []
        with ThreadPoolExecutor(max_workers=5) as pool:
            # ~55 employer careers sites: re-read at most every 15 minutes (unless Refresh is
            # pressed) so page loads stay quick and the sites don't rate-limit us.
            boards_fut = pool.submit(self.boards.jobs, force, max(max_age, BOARDS_MIN_AGE)) if self.boards else None
            if self.reed_key:
                tasks["Reed"] = pool.submit(fetch_reed, self.reed_key, q)
            if self.adzuna_id and self.adzuna_key:
                tasks["Adzuna"] = pool.submit(self._adzuna_cached, q, force)
            if self.careerjet_key:
                tasks["Careerjet"] = pool.submit(fetch_careerjet, self.careerjet_key, q, user_ip, user_agent)
            if self.jooble_key:
                tasks["Jooble"] = pool.submit(fetch_jooble, self.jooble_key, q)
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
                employer_jobs = [dict(j, sector=classify(j["title"], j["description"][:300]),
                                      level=level_of(j["title"], "", "graduate")) for j in employer_jobs]
                # With dozens of careers sites a few are always slow or down; only tell visitors
                # when a big share fails (the rest is in the logs).
                if failed and len(failed) > len(self.boards.employers) / 4:
                    errors.append(f"{len(failed)} employer careers sites are not responding right now")
                elif failed:
                    log.info("Employer careers sites not responding: %s", ", ".join(failed))

        schemes = [dict(j, sector=classify(j["title"]), salary_annual=j["salary_min"], level="graduate")
                   for j in curated_jobs()]
        if q:
            schemes = [j for j in schemes if _matches(j, q)]
            employer_jobs = [j for j in employer_jobs if _matches(j, q)]

        # The hand-checked scheme list and an employer's own feed can list the same role under
        # slightly different titles ("2027 - Global Investment Banking Analyst Program - Full-Time
        # - London"). Keep the live listing, carrying over the scheme list's closing date.
        employer_jobs = list(employer_jobs)
        kept_schemes = []
        for sch in schemes:
            twin = next((j for j in employer_jobs if same_role(sch, j)), None)
            if twin:
                twin["deadline"] = twin.get("deadline") or sch["deadline"]
            else:
                kept_schemes.append(sch)
        schemes = kept_schemes

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
        # Starter roles only: drop senior roles, and roles nothing marks as graduate or entry level.
        merged = [j for j in merged if j["source"] == "scheme"
                  or (j.get("level") and not too_senior(j["title"], j.get("description", ""))
                      and not is_course_ad(j["title"], j.get("description", "")))]

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

    def _adzuna_cached(self, q: str, force: bool) -> list[dict]:
        """Adzuna results are reused for 30 minutes to stay inside its rate limits."""
        key = q.lower()
        with self._lock:
            hit = self._adzuna_cache.get(key)
        if hit and not force and time.time() - hit[0] < ADZUNA_TTL:
            return hit[1]
        try:
            jobs = fetch_adzuna(self.adzuna_id, self.adzuna_key, q)
        except Exception:
            if hit:  # Adzuna refused us: show the last good results rather than nothing
                return hit[1]
            raise
        with self._lock:
            self._adzuna_cache[key] = (time.time(), jobs)
        return jobs

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
