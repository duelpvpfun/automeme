"""Entry point: `python -m automeme`.

Boots the database, seeds default settings, registers discovery sources, starts
the autonomous scheduler, and serves the private control panel.
"""

from __future__ import annotations

import os

import uvicorn

from .config import get_config
from .control.app import create_app


def main() -> None:
    cfg = get_config()
    app = create_app(start_scheduler=True)
    # Cloud platforms (Render, Railway, etc.) inject the port via $PORT and
    # require binding to 0.0.0.0. Honor $PORT when present.
    port = int(os.environ.get("PORT", cfg.port))
    host = "0.0.0.0" if os.environ.get("PORT") else cfg.host
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
