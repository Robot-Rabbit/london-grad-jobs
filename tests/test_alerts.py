import re

import pytest

from alerts import AlertError, Alerts, Mailer, MemoryStore, matches
from app import create_app
from feeds import JobFeed


def job(i, title, sector="Technology", posted="2026-10-05"):
    return {"id": f"reed:{i}", "employer": "Acme", "title": title, "area": "London", "sector": sector,
            "description": "", "url": f"https://example.com/{i}", "posted": posted, "salary_label": "", "deadline": None}


@pytest.fixture
def alerts():
    return Alerts(MemoryStore(), Mailer())


def token_from(mail):
    return re.search(r"token=([A-Za-z0-9_-]+)", mail["text"]).group(1)


def test_full_alert_lifecycle(alerts):
    alerts.create("Student@Example.com ", "software", "Technology", "https://site/")
    confirm_mail = alerts.mailer.outbox[-1]
    assert confirm_mail["to"] == "student@example.com"
    assert "https://site/alerts/confirm?token=" in confirm_mail["text"]
    token = token_from(confirm_mail)

    # Not confirmed yet: the digest ignores it.
    assert alerts.run_digest([job(1, "Graduate Software Engineer")], "https://site/")["emails"] == 0

    existing = [job(1, "Graduate Software Engineer")]
    alerts.confirm(token, existing)
    # Roles already listed at confirmation aren't sent as "new".
    assert alerts.run_digest(existing, "https://site/")["emails"] == 0

    today = existing + [job(2, "Junior Software Developer"), job(3, "Graduate Accountant", "Audit & accounting")]
    stats = alerts.run_digest(today, "https://site/")
    assert stats == {"alerts": 1, "emails": 1, "roles": 1, "errors": 0}
    digest = alerts.mailer.outbox[-1]
    assert "Junior Software Developer" in digest["text"] and "Accountant" not in digest["text"]
    assert digest["subject"].startswith("1 new London graduate role")
    assert "alerts/unsubscribe?token=" in digest["html"]

    # Running again (Vercel can deliver a cron twice) sends nothing new.
    assert alerts.run_digest(today, "https://site/")["emails"] == 0

    alerts.unsubscribe(token)
    assert alerts.by_token(token) is None
    assert alerts.run_digest(today + [job(4, "Software Engineer, New Grad")], "https://site/")["alerts"] == 0


def test_validation_and_limits(alerts):
    with pytest.raises(AlertError):
        alerts.create("not-an-email", "", "All", "https://site/")
    for _ in range(5):
        alerts.create("a@b.co", "", "All", "https://site/")
        alerts.confirm(token_from(alerts.mailer.outbox[-1]), [])
    with pytest.raises(AlertError, match="already have 5"):
        alerts.create("a@b.co", "", "All", "https://site/")
    assert alerts.by_token("nonsense") is None and alerts.by_token("../../etc") is None


def test_one_failing_email_does_not_stop_others(alerts):
    for email in ("x@b.co", "y@b.co"):
        alerts.create(email, "", "All", "https://site/")
        alerts.confirm(token_from(alerts.mailer.outbox[-1]), [])
    sent = []

    def flaky(to, *a, **k):
        if to == "x@b.co":
            raise RuntimeError("bounce")
        sent.append(to)

    alerts.mailer.send = flaky
    stats = alerts.run_digest([job(9, "Graduate Analyst")], "https://site/")
    assert stats["errors"] == 1 and sent == ["y@b.co"]


def test_matches():
    a = {"keywords": "data analyst", "sector": "All"}
    assert matches(a, job(1, "Graduate Data Analyst"))
    assert not matches(a, job(2, "Graduate Data Engineer"))
    assert not matches({"keywords": "", "sector": "Law"}, job(3, "Trainee", "Finance"))


def test_routes(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret-value-123456")
    store_alerts = Alerts(MemoryStore(), Mailer())
    c = create_app(JobFeed(), store_alerts).test_client()

    r = c.post("/api/alerts", json={"email": "s@uni.ac.uk", "keywords": "law", "sector": "All"})
    assert r.status_code == 200 and "s@uni.ac.uk" in r.get_json()["message"]
    token = token_from(store_alerts.mailer.outbox[-1])

    # GET only shows a button: scanners that open links don't confirm anything.
    page = c.get(f"/alerts/confirm?token={token}")
    assert b"Confirm alert" in page.data and not store_alerts.by_token(token)["confirmed"]
    assert b"Alert confirmed" in c.post("/alerts/confirm", data={"token": token}).data
    assert store_alerts.by_token(token)["confirmed"]

    assert c.get("/api/cron/alerts").status_code == 401
    assert c.get("/api/cron/alerts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = c.get("/api/cron/alerts", headers={"Authorization": "Bearer s3cret-value-123456"})
    assert ok.status_code == 200 and ok.get_json()["alerts"] == 1

    assert b"Unsubscribe" in c.get(f"/alerts/unsubscribe?token={token}").data
    assert b"Unsubscribed" in c.post("/alerts/unsubscribe", data={"token": token}).data
    assert b"no longer exists" in c.get(f"/alerts/unsubscribe?token={token}").data


def test_cron_refuses_when_no_secret_set(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    c = create_app(JobFeed(), Alerts(MemoryStore(), Mailer())).test_client()
    assert c.get("/api/cron/alerts", headers={"Authorization": "Bearer "}).status_code == 401


def test_alert_form_hidden_until_email_and_storage_set_up(monkeypatch):
    for k in ("SENDGRID_API_KEY", "ALERTS_FROM_EMAIL", "UPSTASH_REDIS_REST_URL", "KV_REST_API_URL"):
        monkeypatch.delenv(k, raising=False)
    c = create_app(JobFeed()).test_client()
    assert b"alertForm" not in c.get("/").data
    assert c.post("/api/alerts", json={"email": "a@b.co"}).status_code == 503
