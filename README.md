# London Grad Jobs

A search site for London graduate jobs and 2027-intake graduate schemes. It has:

- **Live job feed.** Searches graduate roles within about 15 miles of central London on
  [Reed](https://www.reed.co.uk/developers/jobseeker) (using its `graduate=true` filter) and
  [Adzuna](https://developer.adzuna.com), merges them, removes duplicates and expired
  listings, and caches each search for 15 minutes.
- **Employer careers sites.** Reads the public job-board APIs that employers publish
  through Greenhouse, Lever, Ashby, SmartRecruiters and Workable, plus Workday careers
  sites (Citi, LSEG, Barclays, Morgan Stanley and others). It keeps London roles whose titles look
  graduate-level, such as "Graduate", "New Grad", "Junior" or "2027". The employer list is
  `EMPLOYERS` in `employers.py`. This feed needs no keys, and results are cached for 30 minutes.
- **Graduate scheme snapshot.** A hand-checked list of big-employer schemes (J.P. Morgan,
  Lazard, Shell, FTI and others) in `curated.py`. These rarely appear on Reed or Adzuna,
  so they are merged into every search.
- **AI help (Claude).** Three features:
  - **Smart match** ranks roles against a plain-English request plus your CV.
  - **CV highlights** gives tailored bullets, keywords to mirror and gaps to address.
  - **Cover letter** streams a draft in one of three tones.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Then fill in `.env`:

| Variable | What it turns on | Where to get it |
|---|---|---|
| `REED_API_KEY` | Live Reed listings | https://www.reed.co.uk/developers/jobseeker |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Live Adzuna listings | https://developer.adzuna.com |
| `CAREERJET_API_KEY` | Live Careerjet listings | https://www.careerjet.com/partners/ |
| `JOOBLE_API_KEY` | Live Jooble listings | https://jooble.org/api/about |
| `ANTHROPIC_API_KEY` | AI features | https://platform.claude.com/settings/keys |

Every key is optional:

- With no job-feed keys, the site shows the scheme snapshot only.
- With no Anthropic key, the AI buttons are turned off.

## Run

```bash
venv/bin/python app.py
```

Open http://127.0.0.1:5060.

## Tests

```bash
venv/bin/pytest -q
```

## Notes

- **AI model.** AI calls use `claude-opus-5-5` with server-side refusal fallback turned
  on. They're rate-limited per visitor IP (`AI_LIMIT_PER_HOUR`, default 40).
- **Profile storage.** A user's CV text and shortlist are stored only in their browser.
  The CV is sent to the server, and on to Claude, only when they press an AI button.
- **Updating the snapshot.** Edit `curated.py` as schemes open and close. Expired
  deadlines are hidden automatically.
- **Sectors.** Each job's sector is guessed from its title and description using
  `feeds.SECTOR_RULES`.

## Deploy to Render

`render.yaml` sets up a Python web service in Frankfurt, Render's closest region to
London. It runs gunicorn and has a `/healthz` health check.

1. Push this repo to GitHub.
2. In Render, choose **New → Blueprint** and pick the repo.
3. When asked, enter the four keys. Use freshly issued keys, not ones from `.env`.
4. Click **Apply**.

The free plan sleeps after 15 minutes with no visitors, so the next page load takes
about 30 seconds. Switch the service to a paid plan to keep it awake.

AI spend is capped two ways: per visitor IP (`AI_LIMIT_PER_HOUR`) and site-wide per day
(`AI_DAILY_LIMIT`). Also set a monthly spend limit in the Claude Console.

## Deploy to Vercel

Vercel runs `app.py` as a single function. It serves `public/` from its CDN, and
`vercel.json` allows 60 seconds per request for cover-letter drafts.

1. In Vercel, choose **Add New → Project** and import the GitHub repo. Leave the build
   settings at their defaults.
2. Under **Environment Variables**, add the four keys.
3. Click **Deploy**.

On Vercel, the job-search cache and the AI rate limits are kept in each running
instance's memory, so they're best-effort there. Rely on a spend limit in the Claude
Console as well.

## Email alerts

Visitors can save their current search and sector as a daily email alert.

1. They get a confirmation email, and nothing is sent until they press **Confirm** on the
   page it links to (double opt-in).
2. Once confirmed, a Vercel Cron job (`/api/cron/alerts`, daily at 06:00 UTC) emails any
   new matching roles.
3. Every email has an unsubscribe link and a one-click `List-Unsubscribe` header.
   Unconfirmed alerts expire after 7 days.

Setup, all in Vercel → Project → Settings:

- **Storage:** add **Upstash for Redis** from the Marketplace (Storage tab). It sets
  `UPSTASH_REDIS_REST_URL`/`TOKEN` (or `KV_REST_API_URL`/`TOKEN`) automatically.
- **SendGrid:** create an API key with Mail Send permission, and verify a sender under
  Settings → Sender Authentication. Single Sender works without a domain, but
  authenticating a domain gets much better inbox placement. Set `SENDGRID_API_KEY` and
  `ALERTS_FROM_EMAIL`.
- **`CRON_SECRET`:** any random string of 16+ characters. Vercel sends it to the cron job.
- **`PUBLIC_BASE_URL`:** optional, e.g. `https://london-grad-jobs.vercel.app`, used for
  links in emails.

Without Upstash, alerts are kept in memory (local testing only). Without SendGrid,
emails are written to the log instead of being sent.
