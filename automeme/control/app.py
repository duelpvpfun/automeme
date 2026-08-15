"""FastAPI control panel: auth, dashboard UI, and JSON API.

Security:
* single-password login, verified in constant time,
* signed, http-only session cookie (itsdangerous),
* every API/route except the login endpoints requires a valid session,
* the scheduler's autonomy is independent of the panel being open.
"""

from __future__ import annotations

import hmac
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .. import scheduler, settings_store
from ..config import get_config
from ..db import init_db
from . import service

_BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))

COOKIE_NAME = "automeme_session"
SESSION_MAX_AGE = 60 * 60 * 12  # 12h


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_config().secret_key, salt="automeme-session")


def _issue_session(resp: Response) -> None:
    token = _serializer().dumps({"auth": True, "ts": int(time.time())})
    resp.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_MAX_AGE, httponly=True,
        samesite="lax", secure=not get_config().dry_run,
    )


def _valid_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, Exception):
        return False


def require_auth(request: Request) -> None:
    if not _valid_session(request):
        raise HTTPException(status_code=401, detail="authentication required")


def create_app(start_scheduler: bool = True) -> FastAPI:
    app = FastAPI(title="automeme control panel", docs_url=None, redoc_url=None)

    static_dir = _BASE / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        settings_store.ensure_defaults()
        # Register discovery sources.
        from .. import discovery  # noqa: F401
        from ..discovery import (  # noqa: F401
            memeapi, reddit, reddit_api, reddit_rss, x_source,
        )

        # Env-driven bootstrap so the bot can go live from environment variables
        # alone (e.g. on Railway, where the DB starts empty at defaults).
        cfg = get_config()
        if cfg.start_mode in (settings_store.MODE_AUTO, settings_store.MODE_APPROVAL):
            settings_store.set_value("mode", cfg.start_mode)
        if cfg.start_unpaused:
            settings_store.set_value("paused", False)

        if start_scheduler:
            scheduler.start()
            # Kick off one discovery cycle right away so the queue fills fast
            # instead of waiting a full interval after boot.
            import threading
            threading.Thread(target=scheduler.run_discovery, daemon=True).start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        scheduler.shutdown()

    # -- auth ----------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse("login.html", {"request": request, "error": None})

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        expected = get_config().panel_password
        if not expected or not hmac.compare_digest(password, expected):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid password."},
                status_code=401,
            )
        resp = RedirectResponse("/", status_code=303)
        _issue_session(resp)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # -- pages ---------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not _valid_session(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse("dashboard.html", {"request": request})

    # -- JSON API ------------------------------------------------------------
    api_dep = [Depends(require_auth)]

    @app.get("/api/stats", dependencies=api_dep)
    def api_stats():
        return service.dashboard_stats()

    @app.get("/api/settings", dependencies=api_dep)
    def api_get_settings():
        return settings_store.get_all()

    @app.post("/api/settings", dependencies=api_dep)
    async def api_set_settings(request: Request):
        body = await request.json()
        return service.update_settings(body or {})

    @app.get("/api/candidates", dependencies=api_dep)
    def api_candidates(status: str | None = None, limit: int = 100):
        return service.list_candidates(status=status, limit=min(limit, 300))

    @app.post("/api/candidates/{cid}/approve", dependencies=api_dep)
    def api_approve(cid: int):
        return {"ok": service.approve(cid)}

    @app.post("/api/candidates/{cid}/reject", dependencies=api_dep)
    def api_reject(cid: int):
        return {"ok": service.reject(cid)}

    @app.post("/api/candidates/{cid}/disable", dependencies=api_dep)
    def api_disable(cid: int):
        return {"ok": service.disable(cid)}

    @app.post("/api/candidates/{cid}/delete_post", dependencies=api_dep)
    def api_delete_post(cid: int):
        return {"ok": service.delete_posted(cid)}

    @app.post("/api/queue/purge", dependencies=api_dep)
    def api_purge():
        return {"purged": service.purge_queue()}

    @app.post("/api/control/pause", dependencies=api_dep)
    async def api_pause(request: Request):
        body = await request.json()
        service.set_paused(bool(body.get("paused", True)))
        return service.dashboard_stats()

    @app.post("/api/control/kill", dependencies=api_dep)
    async def api_kill(request: Request):
        body = await request.json()
        service.set_kill_switch(bool(body.get("active", True)))
        return service.dashboard_stats()

    @app.post("/api/control/mode", dependencies=api_dep)
    async def api_mode(request: Request):
        body = await request.json()
        ok = service.set_mode(str(body.get("mode", "")))
        return {"ok": ok, "stats": service.dashboard_stats()}

    @app.get("/api/blocklist", dependencies=api_dep)
    def api_blocklist():
        return service.list_blocklist()

    @app.post("/api/blocklist", dependencies=api_dep)
    async def api_add_block(request: Request):
        body = await request.json()
        return {"ok": service.add_block(str(body.get("kind", "")), str(body.get("value", "")))}

    @app.delete("/api/blocklist/{bid}", dependencies=api_dep)
    def api_remove_block(bid: int):
        return {"ok": service.remove_block(bid)}

    @app.post("/api/taste", dependencies=api_dep)
    async def api_add_taste(request: Request):
        body = await request.json()
        return {"ok": service.add_taste_exemplar(
            str(body.get("image_url", "")), str(body.get("label", "s8n")))}

    @app.get("/api/activity", dependencies=api_dep)
    def api_activity(limit: int = 200):
        return service.activity(limit=min(limit, 500))

    @app.get("/api/simulate", dependencies=api_dep)
    def api_simulate(days: int = 3):
        from .. import simulate as sim
        return sim.to_dicts(sim.simulate(days=max(1, min(days, 14))))

    @app.get("/api/diagnose", dependencies=api_dep)
    def api_diagnose():
        return service.diagnose()

    @app.post("/api/actions/discover", dependencies=api_dep)
    def api_force_discover():
        return service.force_discovery()

    @app.post("/api/actions/post_now", dependencies=api_dep)
    def api_force_post():
        return service.force_post()

    return app
