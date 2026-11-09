"""Standard-library logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> None:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "tdserial-iot.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
