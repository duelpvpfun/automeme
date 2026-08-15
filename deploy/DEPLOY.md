# Deploying automeme 24/7

automeme is a **long-running background worker** (it discovers + posts on a
schedule) that also serves a small web panel. That one fact drives every hosting
decision: you need a host that **runs continuously** and gives you **persistent
storage** (for the SQLite DB + learned engagement data + downloaded images).

---

## Render vs Railway — which to use

**Short answer: use Railway if you want the easiest setup; use Render if you want
slightly cheaper always-on with a persistent disk.**

| | **Railway** | **Render** |
|---|---|---|
| Setup effort | Easiest (detects Dockerfile, deploy in minutes) | Easy (Blueprint `render.yaml` included) |
| Always-on | Yes (usage-based billing) | Yes on a paid **Web Service** (free tier sleeps — not usable here) |
| Persistent storage | Volume (mount at `/data`) | Disk (mount at `/data`, paid plans only) |
| Best for | "Just get it running" | Predictable flat monthly price |
| Gotcha | Watch usage-based cost | Free tier **sleeps** → breaks the scheduler; must use a paid plan |

**Recommendation:**
- **Railway** — start here. Simplest path, and the always-on worker model fits.
- **Render** — great if you prefer a fixed price; use the included `render.yaml`
  and **attach a disk** at `/data`.
- **A cheap VPS** (Hetzner/DigitalOcean, ~$5/mo) with the included
  `systemd` unit is the cheapest and most controllable option if you're
  comfortable with SSH.

> ⚠️ Do **not** use any "free" tier that sleeps on idle (Render free web service,
> etc.). Sleeping stops discovery and posting. This app must stay awake.

---

## Option A — Railway

1. Push this repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo** → pick `automeme`.
   It auto-detects the `Dockerfile`.
3. **Variables** → add:
   ```
   AUTOMEME_PANEL_PASSWORD = <strong password>
   AUTOMEME_SECRET_KEY     = <random; python -c "import secrets;print(secrets.token_urlsafe(48))">
   AUTOMEME_DRY_RUN        = false
   AUTOMEME_X_API_KEY      = ...
   AUTOMEME_X_API_SECRET   = ...
   AUTOMEME_X_ACCESS_TOKEN = ...
   AUTOMEME_X_ACCESS_SECRET= ...
   AUTOMEME_X_BEARER_TOKEN = ...
   ```
4. **Add a Volume** and mount it at `/data` (keeps the DB across restarts).
5. Deploy. Railway gives you a public URL → that's your panel link.

---

## Option B — Render (Blueprint)

1. Push this repo to GitHub (the included `render.yaml` is the blueprint).
2. Render → **New → Blueprint** → select the repo.
3. It creates a Docker web service with a **1 GB disk mounted at `/data`**.
4. In the dashboard set the secret env vars (`AUTOMEME_PANEL_PASSWORD` and the
   `AUTOMEME_X_*` keys). `AUTOMEME_SECRET_KEY` is auto-generated.
5. Deploy. The service URL is your panel link.

> Render injects `$PORT`; the app already honors it and binds `0.0.0.0`.

---

## Option C — Plain VPS (cheapest, most control)

```bash
# as root or with sudo
adduser --system --group automeme
git clone https://github.com/duelpvpfun/automeme.git /opt/automeme
cd /opt/automeme
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env          # set password, secret, DRY_RUN=false, X_* keys, HOST=127.0.0.1
chown -R automeme:automeme /opt/automeme

sudo cp deploy/automeme.service /etc/systemd/system/automeme.service
sudo systemctl daemon-reload
sudo systemctl enable --now automeme
sudo systemctl status automeme
journalctl -u automeme -f     # watch it work
```

### Reaching the panel safely on a VPS
Keep `AUTOMEME_HOST=127.0.0.1` (not exposed to the internet) and open the panel
through an **SSH tunnel** from your laptop:

```bash
ssh -L 8080:127.0.0.1:8080 youruser@your-vps-ip
# then browse http://127.0.0.1:8080 locally
```

If you'd rather expose it directly, set `AUTOMEME_HOST=0.0.0.0`, put it behind a
reverse proxy with HTTPS (Caddy/nginx), and use a long panel password. Exposing
plain HTTP port 8080 to the world is not recommended.

---

## After deploy — going live checklist

- [ ] `AUTOMEME_DRY_RUN=false` and all four `AUTOMEME_X_*` write keys set.
- [ ] Panel shows **X connected** and **LIVE** (not DRY-RUN).
- [ ] Mode is **auto** (bot posts by itself) — set in Settings or it persists in the DB.
- [ ] Persistent storage mounted at `/data` (Railway volume / Render disk / VPS local).
- [ ] You can log into the panel and see the **Posted** tab filling up.

That's it — it runs itself from here.
