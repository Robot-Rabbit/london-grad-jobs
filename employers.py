"""Graduate roles straight from employers' own job systems.

Greenhouse, Lever, Ashby, SmartRecruiters and Workable publish public, keyless
job-board APIs intended for embedding a company's openings elsewhere. Workday careers
sites load their listings from a public JSON endpoint on the employer's own careers
site; we read it the same way the page does and skip any site that challenges us.
We keep London roles that look graduate-level and normalise them to the shared shape.

To add an employer, find their careers page's hiring platform and board name:
  greenhouse  boards.greenhouse.io/<board>
  lever       jobs.lever.co/<board>
  ashby       jobs.ashbyhq.com/<board>
  smartrecruiters  jobs.smartrecruiters.com/<board>
  workable    apply.workable.com/<board>
  workday     <tenant>.wd<N>.myworkdayjobs.com/<site>  ->  board "<tenant>:<N>:<site>"
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
    # Added in the second sweep
    ("greenhouse", "optiverus", "Optiver"),
    ("greenhouse", "flowtraders", "Flow Traders"),
    ("greenhouse", "xtxmarketstechnologies", "XTX Markets"),
    ("greenhouse", "scaleai", "Scale AI"),
    ("greenhouse", "anthropic", "Anthropic"),
    ("greenhouse", "databricks", "Databricks"),
    ("greenhouse", "datadog", "Datadog"),
    ("greenhouse", "mongodb", "MongoDB"),
    ("greenhouse", "figma", "Figma"),
    ("greenhouse", "graphcore", "Graphcore"),
    ("greenhouse", "dunnhumby", "dunnhumby"),
    ("greenhouse", "sumup", "SumUp"),
    ("greenhouse", "gocardless", "GoCardless"),
    ("ashby", "elevenlabs", "ElevenLabs"),
    ("ashby", "attio", "Attio"),
    ("ashby", "ramp", "Ramp"),
    ("ashby", "lovable", "Lovable"),
    ("workable", "starling-bank", "Starling Bank"),
    ("smartrecruiters", "Experian", "Experian"),
    ("workday", "citi:5:2", "Citi"),
    ("workday", "lseg:3:Careers", "LSEG"),
    ("workday", "db:3:DBWebSite", "Deutsche Bank"),
    ("workday", "barclays:3:External_Career_Site_Barclays", "Barclays"),
    ("workday", "ms:5:External", "Morgan Stanley"),
    ("workday", "gsk:5:GSKCareers", "GSK"),
    ("workday", "blackrock:1:BlackRock_Professional", "BlackRock"),
    ("workday", "mastercard:1:CorporateCareers", "Mastercard"),
    ("workday", "unilever:3:Unilever_Early_Careers", "Unilever"),
    # FTSE 100 and Top 100 Graduate Employers whose careers sites publish open job feeds
    ("workday", "unilever:3:Unilever_Experienced_Professionals", "Unilever"),
    ("workday", "shell:3:shellcareers", "Shell"),
    ("workday", "bpinternational:3:bpCareers", "BP"),
    ("workday", "relx:3:relx", "RELX"),
    ("workday", "diageo:3:Diageo_Careers", "Diageo"),
    ("workday", "iaggbs:103:IAGGBS", "IAG (British Airways group)"),
    ("workday", "landsecurities:3:Landseccareers", "Landsec"),
    ("workday", "mgpru:3:mandgprudential", "M&G"),
    ("workday", "prudential:3:prudential", "Prudential"),
    ("workday", "smithnephew:5:External", "Smith & Nephew"),
    ("workday", "sky:3:sky_careers", "Sky"),
    ("workday", "lloyds:3:Lloyds-of-London", "Lloyd's of London"),
    ("workday", "aviva:1:External", "Aviva"),
    ("workday", "thales:3:Careers", "Thales"),
    ("workday", "pwc:3:Global_Campus_Careers", "PwC"),
    ("workday", "accenture:103:AccentureCareers", "Accenture"),
    ("workday", "linklaters:3:Linklaters", "Linklaters"),
    ("workday", "freshfields:3:FBD_101", "Freshfields"),
    ("workday", "mmc:1:MMC", "Marsh McLennan (Oliver Wyman, Mercer, Marsh)"),
    ("workday", "forvismazarsuk:107:FM_1", "Forvis Mazars"),
    ("smartrecruiters", "InformaGroupPlc", "Informa"),
    ("smartrecruiters", "RentokilInitial1", "Rentokil Initial"),
    ("eightfold", "bcg:bcg.com", "BCG"),
    ("oracle", "jpmc.fa:CX_1001", "J.P. Morgan"),
    ("oracle", "fa-eqid-saasfaprod1.fa.ocs:CX_1", "Marks & Spencer"),
    ("oracle", "hccz.fa.em3:CX_2", "Pearson"),
    ("oracle", "fa-enor-saasfaprod1.fa.ocs:CX", "Phoenix Group"),
    ("oracle", "ekbq.fa.em2:CX_2", "Schroders"),
    ("oracle", "hcog.fa.em2:CX_1", "Intertek"),
]

GRADUATE = re.compile(
    r"graduate|new grad|\bgrads?\b|early careers?|early talent|entry[- ]level|junior|trainee|apprentice|"
    r"analyst programme|analyst program|\b20(26|27)\b",
    re.I,
)
NOT_GRADUATE = re.compile(
    r"\bintern(ship)?\b|placement|summer|spring week|insight|experienced|"
    r"\bsenior\b|\bsr\.?\b|principal|\bstaff\b|\blead\b|head of|director|\bphds?\b",
    re.I,
)


def is_graduate_role(title: str, entry_level: bool = False) -> bool:
    """`entry_level` is the platform's own experience flag, where it has one."""
    return (entry_level or bool(GRADUATE.search(title))) and not NOT_GRADUATE.search(title)


# Places inside Greater London that employers name without saying "London".
LONDON_PLACES = re.compile(
    r"\b(?:london|canary wharf|osterley|isleworth|hounslow|brentford|chiswick|hammersmith|"
    r"croydon|stratford(?![- ]upon)|wembley|uxbridge|stockley park|heathrow|greenwich|islington|camden|southwark|"
    r"westminster|kensington|shoreditch|white city|paddington|holborn|king'?s cross|"
    r"battersea|vauxhall|wimbledon|richmond upon thames|kingston upon thames|ealing|harrow|bromley)\b|"
    r"\b(?:ec[1-4][a-z]?|wc[12][a-z]?|(?:e|n|nw|se|sw|w)1?\d[a-z]?)\s?\d[a-z]{2}\b",
    re.I,
)


def is_london(location: str) -> bool:
    return bool(LONDON_PLACES.search(location or ""))


def _iso_day(value) -> str | None:
    if isinstance(value, (int, float)):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return (value or "")[:10] or None


COUNTRIES = {"united kingdom", "uk", "england", "great britain", "gb", "united states", "usa", "us"}


def tidy_location(location: str) -> str:
    """'London, Montreal, Singapore' -> 'London + 2 other offices'."""
    location = re.sub(r"\s+", " ", location or "").strip()
    # Workday office codes like "GBR-London-5 Canada Square"
    location = re.sub(r"^[A-Z]{3}-London-(.+)$", r"London (\1)", location)
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


def fetch_smartrecruiters(board: str, employer: str, clean) -> list[dict]:
    base = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
    postings, offset = [], 0
    while offset < 1000:
        r = requests.get(base, params={"limit": 100, "offset": offset}, timeout=TIMEOUT)
        r.raise_for_status()
        page = r.json().get("content", [])
        postings += page
        if len(page) < 100:
            break
        offset += 100
    out = []
    for p in postings:
        loc = p.get("location") or {}
        entry = (p.get("experienceLevel") or {}).get("id") == "entry_level"
        if not (is_london(loc.get("city", "")) and is_graduate_role(p.get("name", ""), entry)):
            continue
        detail = requests.get(p["ref"], timeout=TIMEOUT).json()
        sections = ((detail.get("jobAd") or {}).get("sections") or {})
        text = " ".join((sections.get(k) or {}).get("text", "") for k in ("jobDescription", "qualifications"))
        out.append(_job("smartrecruiters", board, employer, p["id"], p["name"], "London",
                        detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{board}/{p['id']}",
                        _iso_day(p.get("releasedDate")), clean(text)))
    return out


def fetch_workable(board: str, employer: str, clean) -> list[dict]:
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{board}",
                     params={"details": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        locs = [j.get("city") or ""] + [l.get("city", "") for l in j.get("locations") or []]
        entry = (j.get("experience") or "").lower() == "entry level"
        if any(is_london(l) for l in locs) and is_graduate_role(j.get("title", ""), entry):
            out.append(_job("workable", board, employer, j["shortcode"], j["title"], "London", j.get("url"),
                            j.get("published_on") or j.get("created_at"), clean(j.get("description", ""))))
    return out


WORKDAY_SEARCHES = ("graduate london", "junior london", "trainee london", "2027 london")
WORKDAY_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
WORKDAY_MAX = 400  # London postings read per employer


def _workday_london_facet(facets) -> tuple[str, list[str]] | None:
    """Find the location filter and its London values. Workday nests them, e.g.
    locationMainGroup -> locations -> "London - Shell Centre"."""
    for f in facets or []:
        ids = [v["id"] for v in f.get("values", []) if v.get("id") and is_london(v.get("descriptor", ""))]
        if ids and "location" in (f.get("facetParameter") or "").lower():
            return f["facetParameter"], ids
        nested = [v for v in f.get("values", []) if "values" in v]
        found = _workday_london_facet(nested)
        if found:
            return found
    return None


def fetch_workday(board: str, employer: str, clean) -> list[dict]:
    tenant, n, site = board.split(":")
    host = f"https://{tenant}.wd{n}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"

    def page(body):
        r = requests.post(f"{api}/jobs", json=body, headers=WORKDAY_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()  # a bot challenge or block lands here and the board is skipped
        return r.json()

    found = {}

    def keep(postings):
        for p in postings:
            if p.get("externalPath"):  # some postings come back without one; they can't be opened
                found.setdefault(p["externalPath"], p)

    first = page({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    facet = _workday_london_facet(first.get("facets"))
    if facet:
        # Every London posting, filtered by title here: catches "Junior …" and "Trainee …" roles too.
        param, ids = facet
        body = {"appliedFacets": {param: ids}, "limit": 20, "searchText": ""}
        london = page(dict(body, offset=0))
        keep(london.get("jobPostings", []))
        total = min(int(london.get("total") or 0), WORKDAY_MAX)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for data in pool.map(lambda off: page(dict(body, offset=off)), range(20, total, 20)):
                keep(data.get("jobPostings", []))
    else:
        # No London location value to filter on: fall back to keyword searches.
        for text in WORKDAY_SEARCHES:
            for offset in range(0, 60, 20):
                data = page({"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": text})
                keep(data.get("jobPostings", []))
                if offset + 20 >= (data.get("total") or 0):
                    break

    # "3 Locations" hides whether London is one of them, so check those in the detail.
    candidates = [p for p in found.values() if is_graduate_role(p.get("title", ""))
                  and (facet or is_london(p.get("locationsText", "")) or p.get("locationsText", "").endswith("Locations"))]

    def detail(p):
        return p, requests.get(api + p["externalPath"], headers=WORKDAY_HEADERS, timeout=TIMEOUT).json().get("jobPostingInfo", {})

    out = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for p, info in pool.map(detail, candidates):
            locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
            loc = next((l for l in locs if is_london(l)), None)
            if not loc:
                continue
            out.append(_job("workday", tenant, employer, info.get("jobReqId") or p["externalPath"], p["title"], loc,
                            info.get("externalUrl") or f"{host}/{site}{p['externalPath']}",
                            info.get("startDate"),  # the date the posting first went live
                            clean(info.get("jobDescription", ""))))
    return out


ORACLE_HEADERS = {"Accept": "application/json"}
ORACLE_MAX = 500


def fetch_oracle(board: str, employer: str, clean) -> list[dict]:
    """Oracle Recruiting Cloud career sites. `board` is "<host>:<site>", where the careers
    site lives at https://<host>.oraclecloud.com/hcmUI/CandidateExperience/en/sites/<site>."""
    host, site = board.split(":")
    base = f"https://{host}.oraclecloud.com"
    api = f"{base}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    def search(extra: str, limit: int, offset: int = 0) -> dict:
        finder = f"findReqs;siteNumber={site},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC{extra}"
        r = requests.get(api, params={"onlyData": "true", "expand": "requisitionList.secondaryLocations,locationsFacet",
                                      "finder": finder}, headers=ORACLE_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        items = r.json().get("items") or [{}]
        return items[0]

    # The location filter takes ids, so look up the London ones first.
    facets = search(",facetsList=LOCATIONS,keyword=London", 1).get("locationsFacet") or []
    london_ids = [f["Id"] for f in facets if is_london(f.get("Name", ""))]
    found = {}

    def london_pages(lid):
        first = search(f",selectedLocationsFacet={lid}", 100)
        total = min(int(first.get("TotalJobsCount") or 0), ORACLE_MAX)
        pages = [first] + [search(f",selectedLocationsFacet={lid}", 100, off) for off in range(100, total, 100)]
        return [q for p in pages for q in (p.get("requisitionList") or [])]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for reqs in pool.map(london_pages, london_ids):
            for q in reqs:
                found.setdefault(q["Id"], q)
    out = []
    for q in found.values():
        title = q.get("Title", "")
        locs = [q.get("PrimaryLocation", "")] + [l.get("Name", "") for l in q.get("secondaryLocations") or []]
        loc = next((l for l in locs if is_london(l)), None)
        if loc and is_graduate_role(title):
            out.append(_job("oracle", host.split(".")[0], employer, q["Id"], title, loc.title(),
                            f"{base}/hcmUI/CandidateExperience/en/sites/{site}/job/{q['Id']}",
                            q.get("PostedDate"), clean(q.get("ShortDescriptionStr", ""))))
    return out


def fetch_eightfold(board: str, employer: str, clean) -> list[dict]:
    """Eightfold career sites that allow public reads. `board` is "<subdomain>:<company domain>"."""
    sub, domain = board.split(":")
    api = f"https://{sub}.eightfold.ai/api/apply/v2/jobs"
    out, start = [], 0
    while start < 500:
        r = requests.get(api, params={"domain": domain, "start": start, "num": 100, "location": "London",
                                      "sort_by": "timestamp"}, timeout=TIMEOUT)
        r.raise_for_status()
        positions = r.json().get("positions", [])
        for p in positions:
            locs = [p.get("location", "")] + list(p.get("locations") or [])
            loc = next((l for l in locs if is_london(l)), None)
            if loc and is_graduate_role(p.get("name", "")):
                created = p.get("t_create")
                posted = datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat() if created else None
                out.append(_job("eightfold", sub, employer, p["id"], p["name"], loc,
                                p.get("canonicalPositionUrl"), posted, clean(p.get("job_description", ""))))
        if len(positions) < 100:
            break
        start += 100
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "workday": fetch_workday,
    "oracle": fetch_oracle,
    "eightfold": fetch_eightfold,
}


class EmployerBoards:
    """Fetches every board in parallel; one cached result shared by all searches."""

    def __init__(self, clean, employers=EMPLOYERS):
        self.clean = clean
        self.employers = employers
        self._lock = threading.Lock()
        self._cached: tuple[float, list[dict], list[str]] | None = None

    def jobs(self, force: bool = False, max_age: float = CACHE_TTL) -> tuple[list[dict], list[str]]:
        with self._lock:
            if self._cached and not force and time.time() - self._cached[0] < max_age:
                return self._cached[1], self._cached[2]

        def one(entry):
            platform, board, name = entry
            try:
                return FETCHERS[platform](board, name, self.clean), None
            except Exception as e:  # a dead board shouldn't take the rest down
                log.warning("%s board %s failed: %s", platform, board, e)
                return [], name

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(one, self.employers))
        jobs = [j for js, _ in results for j in js]
        failed = [name for _, name in results if name]
        with self._lock:
            self._cached = (time.time(), jobs, failed)
        return jobs, failed
