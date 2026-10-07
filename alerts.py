"""Daily email alerts for new roles matching a saved search.

Flow: a visitor saves a search with their email -> we email a confirmation link
(double opt-in) -> once confirmed, a daily cron emails any new matching roles, each
email carrying a one-click unsubscribe link. Unconfirmed alerts expire after 7 days.

Storage is Upstash Redis over its REST API (set up from Vercel's Marketplace). Without
it the app falls back to in-process memory, which is fine for local testing only.
Email goes out through SendGrid; without a key, emails are logged instead of sent.
"""

import html
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

TIMEOUT = 10
UNCONFIRMED_TTL = 7 * 24 * 3600
SEEN_TTL = 90 * 24 * 3600
MAX_ALERTS_PER_EMAIL = 5
MAX_ROLES_PER_EMAIL = 25
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[a-z]{2,}$", re.I)


class AlertError(Exception):
    """A user-presentable problem."""


# ---------------------------------------------------------------- storage
class RedisStore:
    """Tiny Upstash REST client: just the commands we use."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}

    def cmd(self, *args):
        r = requests.post(self.url, json=[str(a) for a in args], headers=self.headers, timeout=TIMEOUT)
        data = r.json()
        if "error" in data:
            raise RuntimeError(data["error"])
        return data["result"]

    def get(self, key):
        return self.cmd("GET", key)

    def set(self, key, value, ex=None, nx=False):
        args = ["SET", key, value] + (["EX", ex] if ex else []) + (["NX"] if nx else [])
        return self.cmd(*args) == "OK"

    def delete(self, *keys):
        return self.cmd("DEL", *keys)

    def persist(self, key):
        return self.cmd("PERSIST", key)

    def sadd(self, key, *members):
        return self.cmd("SADD", key, *members) if members else 0

    def srem(self, key, *members):
        return self.cmd("SREM", key, *members)

    def smembers(self, key):
        return set(self.cmd("SMEMBERS", key) or [])

    def expire(self, key, seconds):
        return self.cmd("EXPIRE", key, seconds)


class MemoryStore:
    """Same interface, kept in this process. Local development and tests only."""

    def __init__(self):
        self.data, self.expiry = {}, {}

    def _live(self, key):
        if key in self.expiry and self.expiry[key] < time.time():
            self.data.pop(key, None)
            self.expiry.pop(key, None)
        return key in self.data

    def get(self, key):
        return self.data.get(key) if self._live(key) else None

    def set(self, key, value, ex=None, nx=False):
        if nx and self._live(key):
            return False
        self.data[key] = str(value)
        if ex:
            self.expiry[key] = time.time() + ex
        else:
            self.expiry.pop(key, None)
        return True

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)
            self.expiry.pop(k, None)

    def persist(self, key):
        self.expiry.pop(key, None)

    def sadd(self, key, *members):
        self._live(key)
        s = self.data.setdefault(key, set())
        s.update(members)

    def srem(self, key, *members):
        self.data.get(key, set()).difference_update(members)

    def smembers(self, key):
        return set(self.data.get(key, set())) if self._live(key) else set()

    def expire(self, key, seconds):
        self.expiry[key] = time.time() + seconds


def make_store():
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if url and token:
        return RedisStore(url, token)
    log.warning("No Upstash Redis configured: alerts are stored in memory and will be lost on restart.")
    return MemoryStore()


# ---------------------------------------------------------------- email
class Mailer:
    def __init__(self, api_key=None, from_email=None, from_name="London Grad Jobs"):
        self.api_key = api_key
        self.from_email = from_email
        self.from_name = from_name
        self.outbox = []  # what was sent (or would have been), for tests and local runs

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.from_email)

    def send(self, to: str, subject: str, text: str, html_body: str, unsubscribe_url: str | None = None):
        msg = {"to": to, "subject": subject, "text": text, "html": html_body}
        self.outbox.append(msg)
        if not self.configured:
            log.info("Email not sent (SendGrid not configured): %s -> %s", subject, to)
            return
        body = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self.from_email, "name": self.from_name},
            "subject": subject,
            "content": [{"type": "text/plain", "value": text}, {"type": "text/html", "value": html_body}],
        }
        if unsubscribe_url:
            # Lets Gmail/Outlook show their own one-click "Unsubscribe" button.
            body["headers"] = {"List-Unsubscribe": f"<{unsubscribe_url}>",
                               "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        r = requests.post("https://api.sendgrid.com/v3/mail/send", json=body,
                          headers={"Authorization": f"Bearer {self.api_key}"}, timeout=TIMEOUT)
        if r.status_code != 202:
            raise RuntimeError(f"SendGrid returned {r.status_code}: {r.text[:200]}")


# ---------------------------------------------------------------- alerts
def describe(alert: dict) -> str:
    parts = []
    if alert.get("keywords"):
        parts.append(f"“{alert['keywords']}”")
    if alert.get("sector") and alert["sector"] != "All":
        parts.append(alert["sector"])
    return " in ".join(parts) if parts else "all graduate roles"


def matches(alert: dict, job: dict) -> bool:
    if alert.get("sector") and alert["sector"] != "All" and job.get("sector") != alert["sector"]:
        return False
    terms = (alert.get("keywords") or "").lower().split()
    hay = f"{job['employer']} {job['title']} {job['area']} {job.get('sector', '')} {job.get('description', '')}".lower()
    return all(t in hay for t in terms)


class Alerts:
    def __init__(self, store, mailer: Mailer):
        self.store = store
        self.mailer = mailer

    # --- keys
    @staticmethod
    def _k(*parts):
        return "alerts:" + ":".join(parts)

    def _load(self, alert_id):
        raw = self.store.get(self._k("alert", alert_id))
        return json.loads(raw) if raw else None

    def _save(self, alert, ex=None):
        self.store.set(self._k("alert", alert["id"]), json.dumps(alert), ex=ex)

    def by_token(self, token: str):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
            return None
        alert_id = self.store.get(self._k("token", token))
        return self._load(alert_id) if alert_id else None

    # --- lifecycle
    def create(self, email: str, keywords: str, sector: str, base_url: str) -> dict:
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise AlertError("That email address doesn't look right. Check it and try again.")
        keywords = re.sub(r"\s+", " ", (keywords or "")).strip()[:80]
        sector = (sector or "All").strip()[:40]
        existing = self.store.smembers(self._k("email", email))
        if len(existing) >= MAX_ALERTS_PER_EMAIL:
            raise AlertError(f"You already have {MAX_ALERTS_PER_EMAIL} alerts. Unsubscribe from one to add another.")

        alert = {
            "id": secrets.token_hex(8),
            "token": secrets.token_urlsafe(24),
            "email": email,
            "keywords": keywords,
            "sector": sector,
            "confirmed": False,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save(alert, ex=UNCONFIRMED_TTL)
        self.store.set(self._k("token", alert["token"]), alert["id"], ex=UNCONFIRMED_TTL)

        confirm = f"{base_url}alerts/confirm?token={alert['token']}"
        what = describe(alert)
        self.mailer.send(
            email,
            "Confirm your London Grad Jobs alert",
            f"Please confirm you'd like a daily email of new London graduate roles matching {what}.\n\n"
            f"Confirm: {confirm}\n\nIf you didn't ask for this, ignore this email and nothing will be sent.",
            f"<p>Please confirm you'd like a daily email of new London graduate roles matching "
            f"<b>{html.escape(what)}</b>.</p><p><a href=\"{confirm}\">Confirm my alert</a></p>"
            f"<p style=\"color:#666\">If you didn't ask for this, ignore this email and nothing will be sent.</p>",
        )
        return alert

    def confirm(self, token: str, current_jobs: list[dict]) -> dict | None:
        alert = self.by_token(token)
        if not alert:
            return None
        if not alert["confirmed"]:
            alert["confirmed"] = True
            self._save(alert)
            self.store.persist(self._k("token", token))
            self.store.sadd(self._k("all"), alert["id"])
            self.store.sadd(self._k("email", alert["email"]), alert["id"])
            # Roles already listed today aren't "new": the first digest starts from here.
            self._mark_seen(alert["id"], [j["id"] for j in current_jobs if matches(alert, j)])
        return alert

    def unsubscribe(self, token: str) -> dict | None:
        alert = self.by_token(token)
        if not alert:
            return None
        self.store.delete(self._k("alert", alert["id"]), self._k("token", token), self._k("seen", alert["id"]))
        self.store.srem(self._k("all"), alert["id"])
        self.store.srem(self._k("email", alert["email"]), alert["id"])
        return alert

    def _mark_seen(self, alert_id, job_ids):
        key = self._k("seen", alert_id)
        if job_ids:
            self.store.sadd(key, *job_ids)
        self.store.expire(key, SEEN_TTL)

    # --- daily run
    def run_digest(self, jobs: list[dict], base_url: str) -> dict:
        """Email each confirmed alert its unseen matching roles. Safe to run twice."""
        if not self.store.set(self._k("lock"), "1", ex=15 * 60, nx=True):
            return {"skipped": "another run is in progress"}
        stats = {"alerts": 0, "emails": 0, "roles": 0, "errors": 0}
        try:
            for alert_id in sorted(self.store.smembers(self._k("all"))):
                alert = self._load(alert_id)
                if not alert or not alert.get("confirmed"):
                    self.store.srem(self._k("all"), alert_id)
                    continue
                stats["alerts"] += 1
                seen = self.store.smembers(self._k("seen", alert_id))
                new = [j for j in jobs if matches(alert, j) and j["id"] not in seen]
                if not new:
                    continue
                try:
                    self._send_digest(alert, new, base_url)
                except Exception as e:  # one bad address shouldn't stop everyone else's digest
                    log.warning("Digest for alert %s failed: %s", alert_id, e)
                    stats["errors"] += 1
                    continue
                self._mark_seen(alert_id, [j["id"] for j in new])
                stats["emails"] += 1
                stats["roles"] += len(new)
        finally:
            self.store.delete(self._k("lock"))
        return stats

    def _send_digest(self, alert, new, base_url):
        unsubscribe = f"{base_url}alerts/unsubscribe?token={alert['token']}"
        shown = sorted(new, key=lambda j: j.get("posted") or "", reverse=True)[:MAX_ROLES_PER_EMAIL]
        more = len(new) - len(shown)
        what = describe(alert)
        n = len(new)
        subject = f"{n} new London graduate role{'s' if n != 1 else ''}: {what}"

        def line(j):
            bits = [j["employer"], j["area"], j.get("salary_label") or "", f"closes {j['deadline']}" if j.get("deadline") else ""]
            return " · ".join(b for b in bits if b)

        text = "\n\n".join(f"{j['title']}\n{line(j)}\n{j['url']}" for j in shown)
        rows = "".join(
            f"<tr><td style=\"padding:10px 0;border-bottom:1px solid #e3e8e6\">"
            f"<a href=\"{html.escape(j['url'] or '')}\" style=\"font-weight:600;color:#0d6a5a\">{html.escape(j['title'])}</a>"
            f"<div style=\"color:#5b6a67;font-size:13px\">{html.escape(line(j))}</div></td></tr>"
            for j in shown
        )
        more_text = f"\n\n…and {more} more on the site: {base_url}" if more else ""
        more_html = f"<p>…and {more} more on <a href=\"{base_url}\">the site</a>.</p>" if more else ""
        self.mailer.send(
            alert["email"],
            subject,
            f"New roles matching {what}:\n\n{text}{more_text}\n\nUnsubscribe: {unsubscribe}",
            f"<div style=\"font-family:Arial,sans-serif;max-width:600px\">"
            f"<p>New London graduate roles matching <b>{html.escape(what)}</b>:</p>"
            f"<table style=\"width:100%;border-collapse:collapse\">{rows}</table>{more_html}"
            f"<p style=\"color:#888;font-size:12px;margin-top:24px\">You're getting this because you set up an alert "
            f"on London Grad Jobs. <a href=\"{unsubscribe}\">Unsubscribe</a></p></div>",
            unsubscribe_url=unsubscribe,
        )
