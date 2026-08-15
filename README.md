# automeme

An autonomous X (Twitter) meme-curator, inspired by [@s8n](https://x.com/s8n).

`automeme` continuously discovers fast-growing memes and relatable images from
public sources, screens every candidate through an independent, **fail-closed**
safety pipeline, removes near-duplicates, scores what is most likely to perform,
and posts the strongest picks to your X account throughout the day — image-only,
with little or no caption. It learns from each post's engagement so its taste
sharpens over time, and it can learn a target *style* from reference posts you
feed it (e.g. images from @s8n).

Everything is controlled from a private, password-protected web panel.

> **Safety-first defaults:** the system starts **paused**, in **approval mode**,
> and in **dry-run** (it does everything except actually post). You explicitly
> flip it to live/automatic from the panel when you're ready.

---

## Features

- **Discovery** — pluggable sources; ships with Reddit (public JSON, no auth)
  pulling `rising`/`hot` from curated meme subreddits, ranked by *velocity*
  (growth rate) to catch memes before they're overused.
- **Multi-layer safety** — 16 independent checks. Rejects nudity/graphic
  content, hate/slurs, harassment, scams, misinformation, PII, impersonation,
  political controversy, watermarked ads, crypto contract addresses, suspicious
  links, and internal/system-text leaks. **Anything uncertain is rejected.**
- **Dedup** — perceptual hashing (pHash) blocks duplicate and near-duplicate
  posts, with a permanent memory of everything ever posted.
- **Scoring + learning** — blends velocity, popularity, freshness, style match,
  and a per-source/subject engagement prior learned from your own results.
- **Taste profile** — teach it your style by adding reference image URLs; strong
  performers are automatically promoted into the taste set.
- **Autonomous scheduler** — strict daily caps, minimum spacing, active-hours
  window, randomized jitter for natural timing, and per-source/subject diversity.
- **Guardrails** — emergency stop, pause/resume, instant queue purge, per-post
  delete-from-X, complete activity log, and automatic shutdown after repeated
  errors.
- **Two modes** — *approval* (you approve each post) or *auto* (fully autonomous).

---

## Quick start

```bash
# 1. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
#    edit .env: set AUTOMEME_PANEL_PASSWORD and AUTOMEME_SECRET_KEY
#    generate a secret with:
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Run
python -m automeme
```

Open the panel at `http://127.0.0.1:8080`, log in with your panel password, and
you'll land on the dashboard. It starts **paused / approval / dry-run**.

### Recommended first session

1. Leave `AUTOMEME_DRY_RUN=true`. Click **Resume**. Watch the **Review** tab fill
   with discovered candidates (each already safety-screened and de-duplicated).
2. In **Taste**, paste a few image URLs of posts you love (e.g. from @s8n) so the
   curator learns your style.
3. Approve or disable candidates. In dry-run, "posting" is simulated and logged
   under **Activity** — verify the behavior looks right.
4. When satisfied, add your X credentials (below), set `AUTOMEME_DRY_RUN=false`,
   restart, and optionally switch to **auto** mode.

---

## Connecting your X account (only needed to actually post)

You only need these to publish or read metrics. In dry-run everything works
without them.

1. Create an app at <https://developer.x.com> with **Read and Write** permission.
2. Generate **OAuth 1.0a** user access tokens for the account you want to post
   from.
3. (Optional) Copy the **OAuth2 bearer token** for reading post metrics.
4. Put them in `.env`:

```env
AUTOMEME_X_API_KEY=...
AUTOMEME_X_API_SECRET=...
AUTOMEME_X_ACCESS_TOKEN=...
AUTOMEME_X_ACCESS_SECRET=...
AUTOMEME_X_BEARER_TOKEN=...      # optional, for metrics
AUTOMEME_DRY_RUN=false
```

Restart `python -m automeme`. The dashboard will show **X connected** and **LIVE**.

> ⚠️ **This is the only step that genuinely requires your action / credentials.**

---

## The control panel

| Tab | What it does |
|-----|--------------|
| **Dashboard** | Live status pills (running/paused, mode, dry-run/live, kill switch, X connection) and counts. Master buttons: Pause, Resume, Toggle mode, Purge queue, Emergency stop. |
| **Review** | Approve or disable awaiting/queued candidates (image previews + quality score). |
| **Posted** | Published posts with impressions/likes/reposts/bookmarks and engagement rate; delete any post from X. |
| **Settings** | Posting frequency, active hours, quality thresholds, safety strictness, dedup sensitivity, diversity limits, allowed subjects, auto-shutdown threshold. Bounds are enforced server-side. |
| **Blocklist** | Block sources, topics/subreddits, authors, or phrases. |
| **Taste** | Add reference image URLs to teach your style. |
| **Activity** | Complete, redacted audit log of everything the system does. |

---

## Optional but recommended add-ons

For the strictest possible image screening install these; they are optional and
the system runs without them at `medium` strictness.

```bash
# OCR — reads text baked into images so it can be safety-screened
sudo apt-get install tesseract-ocr
pip install pytesseract

# Pixel-level nudity detection (fail-closed at "high" strictness)
pip install nudenet
```

Then set **Safety strictness = high** in Settings. At `high`, if OCR or the
nudity detector is unavailable, affected content is treated as *uncertain* and
therefore **rejected** — true fail-closed behavior.

---

## How safety works (fail-closed)

Every candidate runs through all checks independently. The overall result is:

- **any** check says REJECT → **rejected**
- **any** check says UNCERTAIN (and `reject_on_uncertainty` is on, the default)
  → **rejected**
- a check that errors is treated as a rejection
- content is posted **only if every check passes**

Checks run again immediately before posting (defense in depth), and the caption
is empty by default (@s8n style), so internal text can never leak into a post.

---

## Architecture

```
automeme/
├── config.py            boot-time config (.env)  — paths, secrets, credentials
├── settings_store.py    runtime settings (DB, editable live from the panel)
├── models.py            SQLAlchemy models (candidates, logs, blocklist, taste…)
├── db.py                engine / session helpers (SQLite + WAL)
├── activity.py          audit logging with secret redaction
├── imaging.py           download + pHash + dimensions + optional OCR
├── dedup.py             perceptual near-duplicate detection
├── discovery/           pluggable sources (base, registry, reddit)
├── safety/              base, registry, checks (16), fail-closed pipeline
├── taste.py             style profile learned from reference exemplars
├── scoring.py           virality/quality scoring
├── learning.py          engagement feedback → priors + taste reinforcement
├── publishing/          X client (tweepy; dry-run aware)
├── pipeline.py          discover → analyze → safety → dedup → score → queue
├── scheduler.py         APScheduler: caps, jitter, active hours, kill switch
├── control/             FastAPI panel: auth, service layer, API, templates, static
└── __main__.py          `python -m automeme`
```

Adding a discovery source: implement a class with a `name` and
`fetch(limit) -> list[DiscoveredItem]`, then `register()` it (see
`discovery/reddit.py`). Adding a safety check: implement `name` +
`run(ctx) -> CheckResult` and `register()` it in `safety/checks.py`.

---

## Configuration reference (`.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `AUTOMEME_PANEL_PASSWORD` | `change-me` | Control-panel login password (**required**). |
| `AUTOMEME_SECRET_KEY` | insecure dev key | Session-cookie signing key (**required**). |
| `AUTOMEME_DRY_RUN` | `true` | If true, never actually posts to X. |
| `AUTOMEME_HOST` / `AUTOMEME_PORT` | `127.0.0.1` / `8080` | Panel bind address. |
| `AUTOMEME_DATA_DIR` | `./data` | Where the DB and images live. |
| `AUTOMEME_X_*` | empty | X API credentials (needed only to post/read metrics). |
| `AUTOMEME_USER_AGENT` | descriptive UA | Sent to public APIs (be polite). |

Behavioral settings (posting rate, active hours, thresholds, safety strictness,
mode, etc.) are **not** in `.env` — they're edited live in the **Settings** tab.

---

## Testing

```bash
source .venv/bin/activate
pip install pytest
python -m pytest
```

The suite (39 tests) covers the safety pipeline (every rejection category +
fail-closed behavior), perceptual dedup, the full ingest cycle with a fake
source, scheduler gating (pause/kill/mode/caps/active-hours + a dry-run post),
the control-panel API (auth enforcement, approve/disable, settings bounds,
emergency controls, blocklist, purge), and the learning/taste loop. Tests run
fully offline in dry-run and never touch your real config.

---

## Operational notes & limits

- Runs continuously; the scheduler is independent of whether the panel is open.
- Bind to `127.0.0.1` (default). If exposing remotely, put it behind HTTPS and a
  trusted proxy — the session cookie is marked `secure` automatically when not
  in dry-run.
- Respect the terms of service and rate limits of every source you enable and of
  X itself. You are responsible for what the account posts; keep a human in the
  loop (approval mode) until you trust its taste.
- `@s8n` cannot be scraped directly without X API access; the taste profile
  therefore learns from reference image URLs you supply plus your own
  best-performing posts.
