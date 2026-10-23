from unittest.mock import patch

import employers
import feeds
from employers import fetch_smartrecruiters, fetch_workable, fetch_workday, tidy_location
from feeds import JobFeed, _any_date, clean_text, fetch_careerjet, fetch_jooble, parse_salary_text


class R:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise feeds.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload


def test_salary_text_and_dates():
    assert parse_salary_text("£28k - £32k") == (28000, 32000)
    assert parse_salary_text("28,000 - 32,000 GBP per annum") == (28000, 32000)
    assert parse_salary_text("£12.50 per hour") == (12.5, 12.5)
    assert parse_salary_text("") == (None, None)
    assert _any_date("2026-10-05T00:00:00.0000000") == "2026-10-05"
    assert _any_date("Mon, 05 Oct 2026 09:30:00 GMT") == "2026-10-05"
    assert _any_date("garbage") is None


def test_jooble():
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, json))
        return R({"totalCount": 1, "jobs": [{
            "id": 77, "title": "Graduate <b>Data</b> Analyst", "company": "Acme", "location": "London",
            "snippet": "SQL&nbsp;and dashboards", "salary": "£30k - £34k", "link": "https://jooble.org/desc/77",
            "updated": "2026-10-04T00:00:00.0000000"}]})

    with patch.object(feeds.requests, "post", side_effect=post):
        jobs = fetch_jooble("KEY", "data")
    assert calls[0][0] == "https://jooble.org/api/KEY"
    assert calls[0][1]["keywords"] == "graduate data" and calls[0][1]["location"] == "London"
    j = jobs[0]
    assert j["title"] == "Graduate Data Analyst" and j["description"] == "SQL and dashboards"
    assert j["salary_label"] == "£30,000–£34,000" and j["posted"] == "2026-10-04"
    assert j["posted_kind"] == "updated" and j["source"] == "jooble"


def test_careerjet():
    seen = {}

    def get(url, params=None, auth=None, timeout=None):
        seen.update(params=params, auth=auth)
        return R({"type": "JOBS", "hits": 1, "pages": 1, "jobs": [{
            "title": "Graduate Marketing Executive", "company": "Brightside", "locations": "London",
            "date": "Mon, 05 Oct 2026 09:30:00 GMT", "description": "Campaigns.", "url": "https://jobviewtrack.com/x",
            "salary_min": 2300, "salary_max": 2500, "salary_type": "M"}]})

    with patch.object(feeds.requests, "get", side_effect=get):
        jobs = fetch_careerjet("CJKEY", "", "81.2.69.160", "Mozilla/5.0")
    assert seen["auth"] == ("CJKEY", "")
    assert seen["params"]["user_ip"] == "81.2.69.160" and seen["params"]["locale_code"] == "en_GB"
    j = jobs[0]
    assert j["salary_label"] == "£2,300–£2,500 per month" and j["salary_annual"] == 30000
    assert j["posted"] == "2026-10-05" and j["id"].startswith("careerjet:")
    with patch.object(feeds.requests, "get", side_effect=get):
        assert fetch_careerjet("CJKEY", "", "", "")[0]["id"] == j["id"]  # stable id


def test_feed_prefers_reed_over_aggregator_duplicates():
    reed = [{"id": "reed:1", "source": "reed", "employer": "Acme", "title": "Graduate Analyst", "area": "London",
             "salary_min": None, "salary_max": None, "salary_label": "", "deadline": None, "posted": None,
             "description": "", "url": "u", "sector": "Finance", "applicants": 3, "level": "graduate"}]
    dup = [dict(reed[0], id="jooble:1", source="jooble", applicants=None)]
    with patch.object(feeds, "fetch_reed", return_value=reed), patch.object(feeds, "fetch_jooble", return_value=dup):
        data = JobFeed(reed_key="k", jooble_key="j").search("analyst")
    live = [j for j in data["jobs"] if j["source"] != "scheme"]
    assert [j["id"] for j in live] == ["reed:1"]
    assert data["live_sources"] == ["reed", "jooble"]


def test_workday_checks_multi_location_roles_in_detail():
    def post(url, json=None, headers=None, timeout=None):
        return R({"total": 3, "jobPostings": [
            {"title": "Analyst Programme 2027", "locationsText": "London  United Kingdom", "externalPath": "/job/a"},
            {"title": "Graduate Operations", "locationsText": "3 Locations", "externalPath": "/job/b"},
            {"title": "Graduate Ops Bangalore", "locationsText": "2 Locations", "externalPath": "/job/c"},
        ]})

    def get(url, headers=None, timeout=None):
        slug = url.rsplit("/", 1)[1]
        info = {"a": {"location": "London  United Kingdom", "startDate": "2026-10-01"},
                "b": {"location": "Leeds", "additionalLocations": ["London"], "startDate": "2026-09-20"},
                "c": {"location": "Bangalore", "additionalLocations": ["Pune"], "startDate": "2026-09-20"}}[slug]
        return R({"jobPostingInfo": dict(info, jobReqId=slug.upper(), jobDescription="<p>Desc</p>",
                                         externalUrl=f"https://x.wd3.myworkdayjobs.com/S/job/{slug}")})

    with patch.object(employers.requests, "post", side_effect=post), \
            patch.object(employers.requests, "get", side_effect=get):
        jobs = fetch_workday("x:3:S", "X Bank", clean_text)
    by_id = {j["id"]: j for j in jobs}
    assert set(by_id) == {"workday:x:A", "workday:x:B"}
    assert by_id["workday:x:A"]["posted"] == "2026-10-01" and by_id["workday:x:A"]["area"] == "London United Kingdom"
    assert by_id["workday:x:A"]["description"] == "Desc"


def test_workday_blocked_board_raises_so_it_is_skipped():
    with patch.object(employers.requests, "post", return_value=R({}, status=403)):
        try:
            fetch_workday("x:3:S", "X", clean_text)
            raise AssertionError("should raise")
        except feeds.requests.HTTPError:
            pass


def test_smartrecruiters_and_workable_use_entry_level_flags():
    def get(url, params=None, timeout=None):
        if "smartrecruiters" in url and url.endswith("/postings"):
            return R({"content": [
                {"id": "1", "name": "Associate Consultant", "location": {"city": "London"},
                 "experienceLevel": {"id": "entry_level"}, "releasedDate": "2026-09-30T10:00:00Z",
                 "ref": "https://api.smartrecruiters.com/v1/companies/E/postings/1"},
                {"id": "2", "name": "Associate Consultant", "location": {"city": "London"},
                 "experienceLevel": {"id": "mid_senior_level"}, "ref": "x"},
            ]})
        if "smartrecruiters" in url:
            return R({"postingUrl": "https://jobs.smartrecruiters.com/E/1",
                      "jobAd": {"sections": {"jobDescription": {"text": "<p>Consulting</p>"}}}})
        return R({"jobs": [
            {"shortcode": "W1", "title": "Customer Analyst", "city": "London", "experience": "Entry level",
             "url": "https://apply.workable.com/j/W1", "published_on": "2026-10-02", "description": "<b>Hi</b>"},
            {"shortcode": "W2", "title": "Customer Analyst", "city": "Manchester", "experience": "Entry level"},
        ]})

    with patch.object(employers.requests, "get", side_effect=get):
        sr = fetch_smartrecruiters("E", "Experian", clean_text)
        wk = fetch_workable("w", "Starling", clean_text)
    assert [j["id"] for j in sr] == ["smartrecruiters:E:1"] and sr[0]["posted"] == "2026-09-30"
    assert sr[0]["description"] == "Consulting"
    assert [j["id"] for j in wk] == ["workable:w:W1"] and wk[0]["posted"] == "2026-10-02"


def test_tidy_workday_locations():
    assert tidy_location("GBR-London-5 Canada Square") == "London (5 Canada Square)"


def test_oracle_reads_london_postings_by_location_id():
    from employers import fetch_oracle

    def get(url, params=None, headers=None, timeout=None):
        finder = params["finder"]
        if "facetsList=LOCATIONS" in finder:
            return R({"items": [{"locationsFacet": [{"Id": 1, "Name": "LONDON, United Kingdom"},
                                                    {"Id": 2, "Name": "Mumbai, India"}]}]})
        assert "selectedLocationsFacet=1" in finder
        return R({"items": [{"TotalJobsCount": 2, "requisitionList": [
            {"Id": "11", "Title": "2027 - Global Investment Banking Analyst Program", "PrimaryLocation": "LONDON, United Kingdom",
             "PostedDate": "2026-09-22", "ShortDescriptionStr": "Join us"},
            {"Id": "12", "Title": "Executive Director, Credit", "PrimaryLocation": "LONDON, United Kingdom", "PostedDate": "2026-09-22"},
        ]}]})

    with patch.object(employers.requests, "get", side_effect=get):
        jobs = fetch_oracle("jpmc.fa:CX_1001", "J.P. Morgan", clean_text)
    assert [j["id"] for j in jobs] == ["oracle:jpmc:11"]
    assert jobs[0]["url"] == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/11"
    assert jobs[0]["posted"] == "2026-09-22" and jobs[0]["area"] == "London, United Kingdom"


def test_eightfold():
    from employers import fetch_eightfold

    def get(url, params=None, timeout=None):
        assert params["domain"] == "bcg.com"
        return R({"positions": [
            {"id": 7, "name": "Associate, Graduate 2027", "location": "London, United Kingdom", "t_create": 1790000000,
             "canonicalPositionUrl": "https://careers.bcg.com/job/7", "job_description": "<p>Consulting</p>"},
            {"id": 8, "name": "Junior Analyst", "location": "Paris, France"}]})

    with patch.object(employers.requests, "get", side_effect=get):
        jobs = fetch_eightfold("bcg:bcg.com", "BCG", clean_text)
    assert [j["id"] for j in jobs] == ["eightfold:bcg:7"] and jobs[0]["posted"] == "2026-09-21"


def test_london_places():
    from employers import is_london
    assert is_london("Osterley") and is_london("Canary Wharf") and is_london("SW1P 2DY")
    assert not is_london("Stratford-upon-Avon") and not is_london("Harrogate") and not is_london("NE1 4XF")


def test_scheme_and_live_listing_merge_keeping_deadline():
    from feeds import same_role
    a = {"employer": "J.P. Morgan", "title": "Global Investment Banking Analyst Program"}
    b = {"employer": "J.P. Morgan", "title": "2027 - Global Investment Banking Analyst Program - Full-Time - London"}
    c = {"employer": "J.P. Morgan", "title": "2027 - Corporate Functions - Corporate Analyst Development Program"}
    assert same_role(a, b) and not same_role(a, c)
