# London Grad Jobs

A search site for London graduate jobs and 2027-intake graduate schemes. It has:

- **Live job feed.** Searches graduate roles within about 15 miles of central London on
  [Reed](https://www.reed.co.uk/developers/jobseeker) (using its `graduate=true` filter) and
  [Adzuna](https://developer.adzuna.com), merges them, removes duplicates and expired
  listings, and caches each search for 15 minutes.
- **Employer careers sites.** Reads the public job-board APIs that employers publish
  through Greenhouse, Lever and Ashby. It keeps London roles whose titles look
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
