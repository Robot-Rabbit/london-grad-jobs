import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import ai
from curated import SNAPSHOT_DATE
from employers import EmployerBoards
from feeds import JobFeed, clean_text

load_dotenv()
logging.basicConfig(level=logging.INFO)

# Marks an error inside a streamed response (a status code can't change mid-stream).
STREAM_ERROR = "\x00"
MAX_PROFILE = 12000
AI_LIMIT_PER_HOUR = int(os.environ.get("AI_LIMIT_PER_HOUR", "40"))
# Site-wide cap on AI calls per day, to bound the Anthropic bill if the site gets busy.
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "500"))


def create_app(feed: JobFeed | None = None) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.feed = feed or JobFeed(
        reed_key=os.environ.get("REED_API_KEY"),
        adzuna_id=os.environ.get("ADZUNA_APP_ID"),
        adzuna_key=os.environ.get("ADZUNA_APP_KEY"),
        boards=EmployerBoards(clean_text),
    )
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

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            live_sources=app.feed.live_sources,
            ai_enabled=ai_enabled,
            snapshot_date=SNAPSHOT_DATE,
        )

    @app.get("/api/jobs")
    def jobs():
        return jsonify(app.feed.search(request.args.get("q", ""), force=request.args.get("refresh") == "1"))

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
