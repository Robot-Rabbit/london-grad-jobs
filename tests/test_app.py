from unittest.mock import patch

import pytest

import feeds
from app import create_app
from feeds import JobFeed, classify

REED_PAYLOAD = {
    "results": [
        {
            "jobId": 111, "employerName": "Acme Analytics", "jobTitle": "Graduate Data Analyst",
            "locationName": "London", "minimumSalary": 30000.0, "maximumSalary": 34000.0,
            "expirationDate": "30/12/2099", "date": "01/10/2026",
            "jobDescription": "<p>Join our <b>data</b> team &amp; learn SQL.</p>",
            "jobUrl": "https://www.reed.co.uk/jobs/111", "applications": 7,
        },
        {
            "jobId": 112, "employerName": "Old Co", "jobTitle": "Graduate Sales Executive",
            "locationName": "London", "minimumSalary": None, "maximumSalary": None,
            "expirationDate": "01/01/2000", "date": "01/12/1999", "jobDescription": "Expired.",
            "jobUrl": "https://www.reed.co.uk/jobs/112",
        },
    ]
}

ADZUNA_PAYLOAD = {
    "results": [
        {
            "id": "9", "title": "Graduate <strong>Marketing</strong> Assistant",
            "company": {"display_name": "Brightside"}, "location": {"display_name": "Camden, London"},
            "salary_min": 27000, "salary_max": 27000, "created": "2026-10-04T09:00:00Z",
            "description": "Social media and brand campaigns.", "redirect_url": "https://adzuna.example/9",
            "category": {"label": "PR, Advertising & Marketing Jobs"},
        },
        {   # duplicate of the Reed job: should be dropped
            "id": "10", "title": "Graduate Data Analyst", "company": {"display_name": "Acme Analytics"},
            "location": {"display_name": "London"}, "created": "2026-10-03T09:00:00Z",
            "description": "dup", "redirect_url": "https://adzuna.example/10",
        },
    ]
}


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def fake_get(url, params=None, auth=None, timeout=None):
    if "reed" in url:
        assert params["graduate"] == "true" and params["locationName"] == "London"
        assert auth == ("reed-key", "")
        return FakeResp(REED_PAYLOAD)
    if "adzuna" in url:
        assert params["where"] == "London" and params["what"].startswith("graduate")
        return FakeResp(ADZUNA_PAYLOAD)
    raise AssertionError(url)


@pytest.fixture
def client():
    feed = JobFeed(reed_key="reed-key", adzuna_id="id", adzuna_key="key")
    with patch.object(feeds.requests, "get", side_effect=fake_get):
        app = create_app(feed)
        app.testing = True
        yield app.test_client()


def test_live_feed_merges_normalises_and_dedupes(client):
    data = client.get("/api/jobs").get_json()
    assert data["live_sources"] == ["reed", "adzuna"]
    by_id = {j["id"]: j for j in data["jobs"]}

    reed = by_id["reed:111"]
    assert reed["description"] == "Join our data team & learn SQL."
    assert reed["salary_label"] == "£30,000–£34,000"
    assert reed["deadline"] == "2099-12-30"
    assert reed["sector"] == "Data & AI"
    assert reed["posted"] == "2026-10-01" and reed["applicants"] == 7

    assert "reed:112" not in by_id  # expired
    assert "adzuna:10" not in by_id  # duplicate of reed:111
    adz = by_id["adzuna:9"]
    assert adz["title"] == "Graduate Marketing Assistant"
    assert adz["salary_label"] == "£27,000"
    assert adz["sector"] == "Marketing & media"

    assert any(j["source"] == "scheme" for j in data["jobs"])


def test_keyword_search_filters_schemes(client):
    data = client.get("/api/jobs?q=actuarial").get_json()
    schemes = [j for j in data["jobs"] if j["source"] == "scheme"]
    assert schemes and all("actuar" in (j["title"] + j["sector"]).lower() for j in schemes)


def test_one_feed_failing_keeps_the_other(client):
    def flaky(url, **kw):
        if "adzuna" in url:
            raise feeds.requests.ConnectionError("down")
        return fake_get(url, **kw)

    with patch.object(feeds.requests, "get", side_effect=flaky):
        data = client.get("/api/jobs?q=x&refresh=1").get_json()
    assert data["errors"] == ["Adzuna is not responding right now"]


def test_no_keys_serves_snapshot():
    app = create_app(JobFeed())
    data = app.test_client().get("/api/jobs").get_json()
    assert data["live_sources"] == []
    assert data["jobs"] and all(j["source"] == "scheme" for j in data["jobs"])


def test_ai_endpoints_need_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    app = create_app(JobFeed())
    r = app.test_client().post("/api/ai/highlights", json={"job_id": "x"})
    assert r.status_code == 503


def test_highlights_uses_feed_job(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    feed = JobFeed(reed_key="reed-key")
    with patch.object(feeds.requests, "get", side_effect=fake_get):
        app = create_app(feed)
        c = app.test_client()
        c.get("/api/jobs")
        with patch("ai.cv_highlights", return_value={"highlights": [], "keywords": [], "gaps": []}) as m:
            r = c.post("/api/ai/highlights", json={"job_id": "reed:111", "profile": "BSc Maths"})
    assert r.status_code == 200
    job, desc, profile = m.call_args.args
    assert job["employer"] == "Acme Analytics" and profile == "BSc Maths"


def test_classify():
    assert classify("Graduate Software Engineer") == "Technology"
    assert classify("Trainee Solicitor") == "Law"
    assert classify("Audit Junior ACA") == "Audit & accounting"


def test_salary_periods():
    from feeds import salary_info
    assert salary_info(85.0, 110.0) == ("£85–£110 per day", 25300)
    assert salary_info(12.5, 12.5)[0] == "£12.50 per hour"
    assert salary_info(30000, 34000) == ("£30,000–£34,000", 34000)
    assert salary_info(None, None) == ("", None)


def test_reed_pages_through_results():
    calls = []

    def paged(url, params=None, auth=None, timeout=None):
        calls.append(params["resultsToSkip"])
        n = 100 if params["resultsToSkip"] < 200 else 50
        return FakeResp({"totalResults": 250, "results": [
            {"jobId": params["resultsToSkip"] + i, "jobTitle": "Graduate Role", "employerName": "E"} for i in range(n)]})

    with patch.object(feeds.requests, "get", side_effect=paged):
        jobs = feeds.fetch_reed("k", "")
    assert sorted(calls) == [0, 100, 200] and len(jobs) == 250


def test_classify_education():
    assert classify("Graduate Teaching Assistant") == "Education"


def test_tidy_area_and_recruitment():
    from feeds import tidy_area
    assert tidy_area("SW1P2DY") == "SW1P 2DY" and tidy_area("cr06na") == "CR0 6NA"
    assert tidy_area("East London") == "East London" and tidy_area("") == "London"
    assert classify("Graduate Recruitment Consultant") == "Sales & business"


def test_classify_more_sectors():
    assert classify("Assistant Quantity Surveyor") == "Construction & property"
    assert classify("Quant Analyst") == "Investment banking"
    assert classify("Trainee Social Worker") == "Public sector & charity"
    assert classify("Graduate Project Manager") == "Operations & project management"


def test_ai_limits(monkeypatch):
    import app as app_module
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(app_module, "AI_LIMIT_PER_HOUR", 2)
    monkeypatch.setattr(app_module, "AI_DAILY_LIMIT", 3)
    c = create_app(JobFeed()).test_client()
    post = lambda ip: c.post("/api/ai/highlights", json={"job_id": "nope"},
                             headers={"X-Forwarded-For": f"1.1.1.1, {ip}"}).status_code
    # Spoofed first entry is ignored: the limit follows the last (proxy-added) address.
    assert [post("9.9.9.9"), post("9.9.9.9"), post("9.9.9.9")] == [404, 404, 429]
    assert post("8.8.8.8") == 404   # third call site-wide
    assert post("7.7.7.7") == 429   # daily cap reached
    assert c.get("/healthz").get_json() == {"ok": True}


def test_page_load_gets_fresh_data_but_not_more_than_once_a_minute():
    import app as app_module
    calls = []
    feed = JobFeed(reed_key="k")
    with patch.object(feeds, "fetch_reed", side_effect=lambda *a: calls.append(1) or []):
        c = create_app(feed).test_client()
        r = c.get("/api/jobs?fresh=1")
        assert r.headers["Cache-Control"] == "no-store"
        c.get("/api/jobs?fresh=1")
        assert len(calls) == 1          # within a minute: served from memory
        feed._cache[""] = (feed._cache[""][0] - app_module.FRESH_MAX_AGE - 1, feed._cache[""][1])
        c.get("/api/jobs?fresh=1")
        assert len(calls) == 2          # older than a minute: fetched again
        c.get("/api/jobs?refresh=1")
        assert len(calls) == 3          # Refresh always fetches
