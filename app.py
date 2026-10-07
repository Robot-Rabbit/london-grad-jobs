import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date

from dotenv import load_dotenv
import hmac

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import ai
from alerts import AlertError, Alerts, Mailer, MemoryStore, describe, make_store
from curated import SNAPSHOT_DATE
from employers import EmployerBoards
import feeds
from feeds import JobFeed, clean_text

load_dotenv()
logging.basicConfig(level=logging.INFO)

# Marks an error inside a streamed response (a status code can't change mid-stream).
STREAM_ERROR = "\x00"
MAX_PROFILE = 12000
FRESH_MAX_AGE = 60  # seconds
AI_LIMIT_PER_HOUR = int(os.environ.get("AI_LIMIT_PER_HOUR", "40"))
# Site-wide cap on AI calls per day, to bound the Anthropic bill if the site gets busy.
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "500"))


def create_app(feed: JobFeed | None = None, alerts: Alerts | None = None) -> Flask:
    # Static files live in public/ so Vercel can serve them from its CDN; Flask serves them locally.
    app = Flask(__name__, static_folder="public/static", static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False
    app.feed = feed or JobFeed(
        reed_key=os.environ.get("REED_API_KEY"),
        adzuna_id=os.environ.get("ADZUNA_APP_ID"),
        adzuna_key=os.environ.get("ADZUNA_APP_KEY"),
        boards=EmployerBoards(clean_text),
        jooble_key=os.environ.get("JOOBLE_API_KEY"),
        careerjet_key=os.environ.get("CAREERJET_API_KEY"),
    )
    app.alerts = alerts or Alerts(
        make_store(),
        Mailer(os.environ.get("SENDGRID_API_KEY"), os.environ.get("ALERTS_FROM_EMAIL")),
    )
    # Only offer alerts when emails can really be sent and alerts will survive a restart
    # (an explicitly passed Alerts, as in tests, counts as set up).
    alerts_enabled = alerts is not None or (
        app.alerts.mailer.configured and not isinstance(app.alerts.store, MemoryStore))
    ai_enabled = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    hits: dict[str, deque] = defaultdict(deque)
    daily = {"day": date.today(), "count": 0}
    limit_lock = threading.Lock()

    def client_ip() -> str:
        # The proxy in front of us appends the real client address last; earlier
        # entries come from the client and can be forged.
        forwarded = request.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[-1].strip() if forwarded else (request.remote_addr or "?")

    def over_limit() -> str | None:
        with limit_lock:
            if daily["day"] != date.today():
                daily.update(day=date.today(), count=0)
            if daily["count"] >= AI_DAILY_LIMIT:
                return "AI help has reached its daily limit for the whole site. It resets at midnight."
            now, q = time.time(), hits[client_ip()]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= AI_LIMIT_PER_HOUR:
                return "You've hit the hourly limit for AI requests. Try again later."
            q.append(now)
            daily["count"] += 1
            return None

    def body():
        data = request.get_json(silent=True) or {}
        return data, str(data.get("profile", ""))[:MAX_PROFILE]

    def ai_guard():
        if not ai_enabled:
            return jsonify(error="AI help isn't set up on this server. Add ANTHROPIC_API_KEY to .env."), 503
        if (msg := over_limit()):
            return jsonify(error=msg), 429
        return None

    def target_job(data):
        job = app.feed.get(str(data.get("job_id", "")))
        if not job and isinstance(data.get("job"), dict):
            # e.g. a shortlisted role from before a server restart: use the browser's copy.
            src = data["job"]
            job = {k: str(src.get(k) or "")[:4000] for k in
                   ("id", "source", "employer", "title", "area", "sector", "salary_label", "deadline", "description")}
        if not job or not job.get("title"):
            return None, None
        pasted = str(data.get("job_description", "")).strip()[:12000]
        return job, pasted or app.feed.full_description(job)

    signups: dict[str, deque] = defaultdict(deque)

    def base_url() -> str:
        return (os.environ.get("PUBLIC_BASE_URL") or request.url_root).rstrip("/") + "/"

    def too_many_signups() -> bool:
        with limit_lock:
            now, q = time.time(), signups[client_ip()]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= 5:
                return True
            q.append(now)
            return False

    @app.post("/api/alerts")
    def create_alert():
        data = request.get_json(silent=True) or {}
        if not alerts_enabled:
            return jsonify(error="Email alerts aren't set up on this site yet."), 503
        if too_many_signups():
            return jsonify(error="Too many alerts set up from here in the last hour. Try again later."), 429
        try:
            alert = app.alerts.create(str(data.get("email", "")), str(data.get("keywords", "")),
                                      str(data.get("sector", "All")), base_url())
        except AlertError as e:
            return jsonify(error=str(e)), 400
        except Exception:
            logging.exception("Creating alert failed")
            return jsonify(error="Couldn't set up the alert right now. Try again shortly."), 502
        return jsonify(ok=True, message=f"Check {alert['email']} for a confirmation link. "
                                        f"Alerts start once you confirm.")

    # Confirm and unsubscribe links open a page with a button (POST) rather than acting on
    # GET, because email security scanners open links automatically.
    @app.route("/alerts/confirm", methods=["GET", "POST"])
    def confirm_alert():
        token = request.values.get("token", "")
        alert = app.alerts.by_token(token)
        if not alert:
            return render_template("alert_page.html", title="Link expired",
                                   message="This confirmation link has expired or was already used to unsubscribe. "
                                           "Set up a new alert from the site."), 404
        if request.method == "POST":
            current = app.feed.search("")["jobs"]
            app.alerts.confirm(token, current)
            return render_template("alert_page.html", title="Alert confirmed",
                                   message=f"You'll get a daily email when new London graduate roles match "
                                           f"{describe(alert)}. Every email has an unsubscribe link.")
        if alert["confirmed"]:
            return render_template("alert_page.html", title="Already confirmed",
                                   message=f"Your alert for {describe(alert)} is already active.")
        return render_template("alert_page.html", title="Confirm your alert",
                               message=f"Send a daily email to {alert['email']} with new roles matching {describe(alert)}?",
                               action="/alerts/confirm", token=token, button="Confirm alert")

    @app.route("/alerts/unsubscribe", methods=["GET", "POST"])
    def unsubscribe_alert():
        token = request.values.get("token", "")
        alert = app.alerts.by_token(token)
        if not alert:
            return render_template("alert_page.html", title="Already unsubscribed",
                                   message="This alert no longer exists. You won't get any more emails from it.")
        if request.method == "POST":  # also the one-click unsubscribe that Gmail and Outlook send
            app.alerts.unsubscribe(token)
            return render_template("alert_page.html", title="Unsubscribed",
                                   message=f"You won't get any more emails for {describe(alert)}.")
        return render_template("alert_page.html", title="Unsubscribe",
                               message=f"Stop daily emails to {alert['email']} for {describe(alert)}?",
                               action="/alerts/unsubscribe", token=token, button="Unsubscribe")

    @app.get("/api/cron/alerts")
    def cron_alerts():
        secret = os.environ.get("CRON_SECRET", "")
        given = request.headers.get("Authorization", "")
        if not secret or not hmac.compare_digest(given, f"Bearer {secret}"):
            return jsonify(error="unauthorised"), 401
        jobs = app.feed.search("", force=True)["jobs"]
        return jsonify(app.alerts.run_digest(jobs, base_url()))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            live_sources=app.feed.live_sources,
            ai_enabled=ai_enabled,
            alerts_email=alerts_enabled,
            snapshot_date=SNAPSHOT_DATE,
        )

    @app.get("/api/jobs")
    def jobs():
        # A page load asks for fresh data (fresh=1): refetch unless it was fetched in the last
        # minute, which stops rapid reloads hammering the job sites. Refresh (refresh=1) always refetches.
        max_age = FRESH_MAX_AGE if request.args.get("fresh") == "1" else feeds.CACHE_TTL
        resp = jsonify(app.feed.search(request.args.get("q", ""), force=request.args.get("refresh") == "1",
                                       user_ip=client_ip(), user_agent=request.headers.get("User-Agent", ""),
                                       max_age=max_age))
        resp.headers["Cache-Control"] = "no-store"  # never let the browser or CDN reuse old listings
        return resp

    @app.post("/api/ai/match")
    def match():
        if (resp := ai_guard()):
            return resp
        data, profile = body()
        jobs = app.feed.search(str(data.get("q", "")))["jobs"]
        if not jobs:
            return jsonify(matches=[])
        try:
            return jsonify(matches=ai.smart_match(str(data.get("request", ""))[:500], profile, jobs))
        except ai.AIError as e:
            return jsonify(error=str(e)), 502

    @app.post("/api/ai/highlights")
    def highlights():
        if (resp := ai_guard()):
            return resp
        data, profile = body()
        job, desc = target_job(data)
        if not job:
            return jsonify(error="That role isn't in the current results. Search again, then retry."), 404
        try:
            return jsonify(ai.cv_highlights(job, desc, profile))
        except ai.AIError as e:
            return jsonify(error=str(e)), 502

    @app.post("/api/ai/cover-letter")
    def cover_letter():
        if (resp := ai_guard()):
            return resp
        data, profile = body()
        job, desc = target_job(data)
        if not job:
            return jsonify(error="That role isn't in the current results. Search again, then retry."), 404

        def gen():
            try:
                yield from ai.cover_letter_stream(job, desc, profile, str(data.get("tone", "")))
            except ai.AIError as e:
                yield STREAM_ERROR + str(e)

        return Response(stream_with_context(gen()), mimetype="text/plain; charset=utf-8",
                        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5060")), debug=True, threaded=True)
