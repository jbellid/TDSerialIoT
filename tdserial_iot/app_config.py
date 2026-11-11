"""Local JSON configuration for the desktop application."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from tdserial_iot.sim7080g import DEFAULT_SIGNAL_QUALITY_THRESHOLDS

logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "port": "COM6",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "flow_control": "Ninguno",
    "initialize_at_on_connect": False,
    "autoscroll": True,
    "display_format": "ASCII",
    "show_special_characters_and_size": False,
    "command_timeout_seconds": 10,
    "command_error_policy": "Detener",
    "monitor_interval": "30 segundos",
    "signal_quality_thresholds": {
        technology: {
            metric: list(boundaries) for metric, boundaries in metrics.items()
        }
        for technology, metrics in DEFAULT_SIGNAL_QUALITY_THRESHOLDS.items()
    },
    "iot_host": "udp.os.1nce.com",
    "iot_port": 4445,
    "iot_apn": "iot.1nce.net",
    "iot_context_id": 0,
    "iot_socket_id": 0,
    "iot_payload_format": "JSON",
    "iot_read_length": 1460,
    "iot_prompt_timeout_ms": 10_000,
    "iot_input_time_ms": 5000,
    "iot_response_timeout_ms": 30_000,
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return deepcopy(DEFAULT_CONFIG)
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("No se pudo leer %s; se usarán los valores predeterminados", CONFIG_PATH)
        return deepcopy(DEFAULT_CONFIG)
    if not isinstance(stored, dict):
        logger.error("La configuración local no es un objeto JSON; se usarán valores predeterminados")
        return deepcopy(DEFAULT_CONFIG)
    return {**deepcopy(DEFAULT_CONFIG), **stored}


def save_config(config: dict[str, Any]) -> None:
    # Crear una copia sin el ICCID (nunca se guarda en archivo)
    config_to_save = {k: v for k, v in config.items() if k != "onence_iccid"}
    try:
        CONFIG_PATH.write_text(
            json.dumps(config_to_save, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.exception("No se pudo guardar la configuración local en %s", CONFIG_PATH)
        raise
