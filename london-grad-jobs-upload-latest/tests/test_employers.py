from unittest.mock import patch

import employers
from employers import EmployerBoards, is_graduate_role
from feeds import JobFeed, clean_text


class R:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


GREENHOUSE = {"jobs": [
    {"id": 1, "title": "Graduate Software Developer", "location": {"name": "London"},
     "absolute_url": "https://boards.greenhouse.io/x/jobs/1", "first_published": "2026-09-30T10:00:00Z",
     "content": "&lt;p&gt;Build &amp;amp; ship trading systems.&lt;/p&gt;"},
    {"id": 2, "title": "Senior Software Developer", "location": {"name": "London"}},
    {"id": 3, "title": "Graduate Quant Developer", "location": {"name": "New York"}},
]}
LEVER = [
    {"id": "abc", "text": "Software Engineer, New Grad", "categories": {"location": "London, UK"},
     "hostedUrl": "https://jobs.lever.co/y/abc", "createdAt": 1790000000000, "descriptionPlain": "Ship it."},
    {"id": "def", "text": "Summer Intern 2027", "categories": {"location": "London"}},
]
ASHBY = {"jobs": [
    {"id": "z1", "title": "Junior ML Engineer", "location": "Remote", "secondaryLocations": [{"location": "London"}],
     "jobUrl": "https://jobs.ashbyhq.com/z/z1", "publishedAt": "2026-10-01T00:00:00Z", "descriptionPlain": "Models."},
]}

BOARDS = [("greenhouse", "gh", "GH Co"), ("lever", "lv", "Lever Co"), ("ashby", "ab", "Ashby Co"), ("greenhouse", "dead", "Dead Co")]


def fake_get(url, params=None, timeout=None):
    if "greenhouse" in url:
        if "/dead/" in url:
            raise employers.requests.ConnectionError("down")
        return R(GREENHOUSE)
    if "lever" in url:
        return R(LEVER)
    if "ashby" in url:
        return R(ASHBY)
    raise AssertionError(url)


def test_boards_keep_only_london_graduate_roles():
    with patch.object(employers.requests, "get", side_effect=fake_get):
        jobs, failed = EmployerBoards(clean_text, BOARDS).jobs()
    by_id = {j["id"]: j for j in jobs}
    assert set(by_id) == {"greenhouse:gh:1", "lever:lv:abc", "ashby:ab:z1"}
    assert failed == ["Dead Co"]
    gh = by_id["greenhouse:gh:1"]
    assert gh["description"] == "Build & ship trading systems."
    assert gh["posted"] == "2026-09-30" and gh["employer"] == "GH Co" and gh["source"] == "employer"
    assert by_id["lever:lv:abc"]["posted"] == "2026-09-21"
    assert by_id["ashby:ab:z1"]["area"] == "London"


def test_boards_are_cached():
    with patch.object(employers.requests, "get", side_effect=fake_get) as m:
        b = EmployerBoards(clean_text, BOARDS[:1])
        b.jobs(); b.jobs()
    assert m.call_count == 1


def test_feed_merges_employer_jobs_and_reports_dead_boards():
    with patch.object(employers.requests, "get", side_effect=fake_get):
        feed = JobFeed(boards=EmployerBoards(clean_text, BOARDS))
        data = feed.search("software")
    assert data["live_sources"] == ["employers"]
    ids = [j["id"] for j in data["jobs"]]
    assert "greenhouse:gh:1" in ids and "lever:lv:abc" in ids and "ashby:ab:z1" not in ids
    assert data["errors"] == ["1 employer job boards are not responding right now"]
    assert feed.get("greenhouse:gh:1")["sector"] == "Technology"


def test_graduate_title_filter():
    assert is_graduate_role("Algorithm Developer (Quant Research & Trading) – 2027 Grads")
    assert is_graduate_role("2027 Graduate Analyst")
    assert not is_graduate_role("Point72 Academy 2026 Investment Analyst Program for Experienced Professionals - UK")
    assert not is_graduate_role("Algorithm Developer – 2027 PhDs")
    assert not is_graduate_role("Senior Junior Mentor Lead")


def test_tidy_location():
    from employers import tidy_location
    assert tidy_location("London, United Kingdom") == "London, United Kingdom"
    assert tidy_location("London, Montreal, Singapore") == "London + 2 other offices"
    assert tidy_location("Chicago, Illinois, United States; London, United Kingdom") == "London + 1 other office"
    assert tidy_location("New York, London") == "London + 1 other office"
