"""Standard logging setup (console + rotating file under data/logs)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("scanner")
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        from .config import get_settings
        log_dir = get_settings().data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "scanner.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:  # logging must never crash the app
        pass
    _CONFIGURED = True
