"""SIM7080G AT commands and response parsers."""

from __future__ import annotations

import csv
import logging
import math
import re
from collections.abc import Mapping, Sequence

logger = logging.getLogger(__name__)

SIM7080G_COMMANDS: dict[str, str] = {
    "ati": "ATI",
    "manufacturer": "AT+CGMI",
    "model": "AT+CGMM",
    "firmware": "AT+CGMR",
    "imei": "AT+CGSN",
    "iccid": "AT+CCID",
    "imsi": "AT+CIMI",
    "sim": "AT+CPIN?",
    "registration": "AT+CEREG?",
    "operator": "AT+COPS?",
    "cpsi": "AT+CPSI?",
    "csq": "AT+CSQ",
    "attach": "AT+CGATT?",
    "pdp": "AT+CGACT?",
    "contexts": "AT+CGDCONT?",
    "ip": "AT+CGPADDR=1",
    "app_context": "AT+CNACT?",
    "sockets": "AT+CASTATE?",
}

FULL_DIAGNOSTIC_KEYS = (
    "ati",
    "manufacturer",
    "model",
    "firmware",
    "imei",
    "iccid",
    "imsi",
    "sim",
    "registration",
    "operator",
    "cpsi",
    "csq",
    "attach",
    "pdp",
    "contexts",
    "ip",
    "app_context",
    "sockets",
)

MONITOR_KEYS = ("registration", "csq", "cpsi", "attach")

_CEREG_STATES = {
    0: "No registrado",
    1: "Registrado en red local",
    2: "Buscando red",
    3: "Registro rechazado",
    4: "Estado desconocido",
    5: "Registrado en roaming",
}
_ACCESS_TECHNOLOGIES = {
    0: "GSM",
    3: "EDGE",
    7: "LTE",
    9: "LTE NB-S1 (IoT)",
}
_CSQ = re.compile(rb"\+CSQ\s*:\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_CPIN = re.compile(rb"\+CPIN\s*:\s*([A-Z ]+)", re.IGNORECASE)
_DIGITS = re.compile(rb"(?<!\d)(\d{14,22})(?!\d)")
_CGATT = re.compile(rb"\+CGATT\s*:\s*(\d+)", re.IGNORECASE)
_CGACT = re.compile(rb"\+CGACT\s*:\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_CEREG = re.compile(rb"\+CEREG\s*:\s*(.*)", re.IGNORECASE)
_SIGNAL_QUALITY_ORDER = {
    "Deficiente": 0,
    "Aceptable": 1,
    "Buena": 2,
    "Excelente": 3,
}

# Orientative thresholds, not universal radio standards. Each RAT owns its
# settings even while both profiles intentionally start with the same values.
DEFAULT_SIGNAL_QUALITY_THRESHOLDS: dict[str, dict[str, tuple[float, float, float]]] = {
    "NB-IoT": {
        "rsrp": (-85, -95, -110),
        "rsrq": (-8, -11, -15),
        "sinr": (20, 13, 0),
    },
    "LTE-M": {
        "rsrp": (-85, -95, -110),
        "rsrq": (-8, -11, -15),
        "sinr": (20, 13, 0),
    },
}
SIGNAL_QUALITY_THRESHOLDS = {
    metric: tuple(zip(boundaries, ("Excelente", "Buena", "Aceptable")))
    for metric, boundaries in DEFAULT_SIGNAL_QUALITY_THRESHOLDS["NB-IoT"].items()
}


def parse_command_response(purpose: str, response_lines: list[bytes]) -> dict[str, str]:
    """Parse one command's RX lines using SIM7080G response formats only."""
    lines = [line.strip(b"\r\n ") for line in response_lines]
    if purpose == "sim":
        return {"sim": parse_sim_state(lines)}
    if purpose == "registration":
        return parse_registration(lines)
    if purpose == "operator":
        return parse_operator(lines)
    if purpose == "cpsi":
        return parse_cpsi(lines)
    if purpose == "csq":
        return parse_csq(lines)
    if purpose == "attach":
        match = next((_CGATT.search(line) for line in lines if _CGATT.search(line)), None)
        if match is None:
            return {"attach": "N/D"}
        state = int(match.group(1))
        return {
            "attach": "Conectado al servicio de paquetes" if state == 1 else "Desconectado"
        }
    if purpose == "pdp":
        contexts = [
            f"CID {int(match.group(1))}: {'activo' if match.group(2) == b'1' else 'inactivo'}"
            for line in lines
            if (match := _CGACT.search(line))
        ]
        return {
            "pdp_state": "; ".join(contexts) if contexts else "N/D",
        }
    if purpose == "contexts":
        contexts = []
        apns = []
        for line in lines:
            fields = _csv_fields(line, b"+CGDCONT:")
            if fields and fields[0].isdigit():
                context_id = fields[0]
                pdp_type = fields[1] if len(fields) > 1 else "N/D"
                apn = fields[2] if len(fields) > 2 and fields[2] else "N/D"
                contexts.append(f"CID {context_id}: {pdp_type}")
                apns.append(f"CID {context_id}: {apn}")
        return {
            "pdp": "; ".join(contexts) if contexts else "N/D",
            "apn": "; ".join(apns) if apns else "N/D",
        }
    if purpose == "ip":
        fields = _first_csv_fields(lines, b"+CGPADDR:")
        return {"ip": ", ".join(fields[1:]) if len(fields) > 1 else "N/D"}
    if purpose == "app_context":
        contexts = []
        for line in lines:
            fields = _csv_fields(line, b"+CNACT:")
            if fields and len(fields) >= 2 and fields[0].isdigit():
                active = fields[1] == "1"
                address = fields[2] if len(fields) > 2 else "N/D"
                contexts.append(
                    f"Contexto de aplicación {fields[0]}: "
                    f"{'activo' if active else 'inactivo'} · IP {address or 'N/D'}"
                )
        return {"app_context": "; ".join(contexts) if contexts else "N/D"}
    if purpose == "sockets":
        active = []
        for line in lines:
            fields = _csv_fields(line, b"+CASTATE:")
            if fields and len(fields) >= 2 and fields[0].isdigit():
                state = fields[1].strip()
                if state not in {"0", "CLOSED", "CLOSE"}:
                    active.append(f"Socket {fields[0]}: estado {state}")
        return {"sockets": "; ".join(active) if active else "Sin conexiones activas"}
    if purpose in {"manufacturer", "model", "firmware", "ati"}:
        content = _non_echo_lines(lines, SIM7080G_COMMANDS.get(purpose, "").encode())
        key = purpose
        if purpose == "ati" and content:
            return {"modem_info": " · ".join(content)}
        return {key: content[0] if content else "N/D"}
    if purpose in {"imei", "iccid", "imsi"}:
        digits = next(
            (match.group(1).decode("ascii") for line in lines if (match := _DIGITS.search(line))),
            "N/D",
        )
        return {purpose: digits}
    return {}


def parse_sim_state(lines: list[bytes]) -> str:
    match = next((_CPIN.search(line) for line in lines if _CPIN.search(line)), None)
    if match is None:
        if any(b"ERROR" in line.upper() for line in lines):
            return "Error de lectura"
        return "N/D"
    state = match.group(1).decode("ascii", errors="replace").strip().upper()
    return {
        "READY": "SIM preparada",
        "SIM PIN": "PIN requerido",
        "SIM PUK": "PUK requerido",
        "NOT INSERTED": "SIM no disponible",
        "NOT READY": "SIM no disponible",
    }.get(state, f"Estado SIM: {state}")


def parse_registration(lines: list[bytes]) -> dict[str, str]:
    for line in lines:
        match = _CEREG.search(line)
        if match is None:
            continue
        fields = [part.strip() for part in match.group(1).split(b",")]
        if not fields:
            break
        # CEREG read responses include <n>,<stat>; the first value is not the state.
        state_field = fields[1] if len(fields) >= 2 else fields[0]
        try:
            state_code = int(state_field)
        except ValueError:
            break
        result = {
            "registration": _CEREG_STATES.get(state_code, "Estado desconocido"),
            "roaming": "Sí" if state_code == 5 else ("No" if state_code in {0, 1, 2, 3} else "N/D"),
        }
        if len(fields) >= 4:
            result["cell"] = fields[3].decode("ascii", errors="replace").strip('"') or "N/D"
        if len(fields) >= 5 and fields[4].isdigit():
            result["access_technology"] = _ACCESS_TECHNOLOGIES.get(
                int(fields[4]), f"Acceso {fields[4].decode('ascii', errors='replace')}"
            )
        return result
    return {"registration": "N/D", "roaming": "N/D"}


def parse_operator(lines: list[bytes]) -> dict[str, str]:
    fields = _first_csv_fields(lines, b"+COPS:")
    if len(fields) < 3:
        return {"operator": "N/D", "operator_code": "N/D", "access_technology": "N/D"}
    operator = fields[2] or "N/D"
    numeric = (
        f"{operator[:3]}-{operator[3:]}"
        if operator.isdigit() and len(operator) in {5, 6}
        else operator
        if operator.isdigit()
        else "N/D"
    )
    technology = "N/D"
    if len(fields) >= 4 and fields[3].isdigit():
        technology = _ACCESS_TECHNOLOGIES.get(int(fields[3]), f"Acceso {fields[3]}")
    return {
        "operator": operator,
        "operator_code": numeric,
        "access_technology": technology,
    }


def parse_csq(lines: list[bytes]) -> dict[str, str]:
    for line in lines:
        match = _CSQ.search(line)
        if match is None:
            continue
        value = int(match.group(1))
        if value == 99 or not 0 <= value <= 31:
            return {"csq": "N/D", "rssi": "N/D", "coverage": "N/D"}
        coverage = (
            "Muy débil"
            if value < 10
            else "Débil"
            if value < 15
            else "Aceptable"
            if value < 20
            else "Buena"
        )
        return {
            "csq": str(value),
            "rssi": f"≈ {-113 + 2 * value} dBm (estimado desde CSQ)",
            "coverage": f"{coverage} (estimado desde CSQ)",
        }
    return {"csq": "N/D", "rssi": "N/D", "coverage": "N/D"}


def parse_cpsi(lines: list[bytes]) -> dict[str, str]:
    fields = _first_csv_fields(lines, b"+CPSI:")
    if not fields:
        return _cpsi_unavailable()
    mode = fields[0]
    operation = fields[1] if len(fields) > 1 else "N/D"
    result = {
        "technology": mode or "N/D",
        "cpsi_state": operation or "N/D",
        "band": "N/D",
        "cell": "N/D",
        "cpsi_mcc_mnc": "N/D",
        "rssi_measured": "N/D",
        "rsrp": "N/D",
        "rsrq": "N/D",
        "rssnr": "N/D",
        "sinr": "N/D",
    }
    if len(fields) < 2:
        return result
    if not mode.upper().startswith("LTE"):
        return result
    if len(fields) < 14:
        return result
    result["cpsi_mcc_mnc"] = fields[2] or "N/D"
    result["cell"] = fields[4] or "N/D"
    band_match = re.search(r"EUTRAN-BAND(\d+)", fields[6], re.IGNORECASE)
    if band_match:
        result["band"] = f"LTE banda {band_match.group(1)} (EARFCN {fields[7]})"
    # SIMCom V1.07, p.131: ...,<RSRQ>,<RSRP>,<RSSI>,<RSSNR>; SINR=2*RSSNR-20.
    # Parse the raw CPSI RSSNR once; downstream classification only reads its SINR value.
    result["rsrq"] = _number_with_unit(fields[10], " dB")
    result["rsrp"] = _number_with_unit(fields[11], " dBm")
    result["rssi_measured"] = _number_with_unit(fields[12], " dBm")
    result["rssnr"] = fields[13] if _is_number(fields[13]) else "N/D"
    rssnr_value = _parse_number(fields[13])
    if rssnr_value is not None:
        sinr_value = 2 * rssnr_value - 20
        if -20 <= sinr_value <= 30:
            result["sinr"] = (
                f"{sinr_value:g} dB (calculado desde RSSNR {rssnr_value:g})"
            )
    return result


def normalize_signal_technology(technology: str | None) -> str | None:
    """Map the CPSI system-mode field to the supported radio technology."""
    normalized = (technology or "").upper().replace("_", "-")
    compact = re.sub(r"[\s-]+", "", normalized)
    if "NB-IOT" in normalized or "NBIOT" in compact:
        return "NB-IoT"
    if "CAT-M1" in normalized or "CATM1" in compact or "LTE-M" in normalized:
        return "LTE-M"
    return None


def resolve_signal_quality_thresholds(
    configured: object,
) -> dict[str, dict[str, tuple[float, float, float]]]:
    """Load independently configurable thresholds, falling back per metric."""
    resolved = {
        technology: dict(metrics)
        for technology, metrics in DEFAULT_SIGNAL_QUALITY_THRESHOLDS.items()
    }
    if not isinstance(configured, Mapping):
        logger.warning("Configuración de umbrales de señal no válida; se usarán valores predeterminados")
        return resolved
    for technology, defaults in DEFAULT_SIGNAL_QUALITY_THRESHOLDS.items():
        configured_metrics = configured.get(technology)
        if not isinstance(configured_metrics, Mapping):
            continue
        for metric in defaults:
            candidate = configured_metrics.get(metric)
            if (
                not isinstance(candidate, Sequence)
                or isinstance(candidate, (str, bytes))
                or len(candidate) != 3
            ):
                if candidate is not None:
                    logger.warning(
                        "Umbrales %s/%s no válidos; se conserva el valor predeterminado",
                        technology,
                        metric,
                    )
                continue
            try:
                values = tuple(float(value) for value in candidate)
            except (TypeError, ValueError):
                logger.warning(
                    "Umbrales %s/%s no numéricos; se conserva el valor predeterminado",
                    technology,
                    metric,
                )
                continue
            if (
                not all(math.isfinite(value) for value in values)
                or not values[0] > values[1] > values[2]
            ):
                logger.warning(
                    "Umbrales %s/%s deben ser finitos y descendentes; "
                    "se conserva el valor predeterminado",
                    technology,
                    metric,
                )
                continue
            resolved[technology][metric] = values
    return resolved


def classify_signal_metric(
    metric: str,
    value: str | float | None,
    thresholds: Sequence[float] | None = None,
) -> str:
    """Classify one metric using [excellent, good, acceptable] boundaries."""
    if thresholds is None:
        defaults = DEFAULT_SIGNAL_QUALITY_THRESHOLDS["NB-IoT"]
        thresholds = defaults.get(metric)
    if thresholds is None or metric not in {"rsrp", "rsrq", "sinr"}:
        raise ValueError(f"Métrica de señal no soportada: {metric}")
    numeric = _parse_number(value)
    if numeric is None:
        return "Desconocida"
    for boundary, quality in zip(thresholds, ("Excelente", "Buena", "Aceptable")):
        if numeric >= boundary:
            return quality
    return "Deficiente"


def classify_signal_quality(
    rsrp: str | float | None,
    rsrq: str | float | None,
    sinr: str | float | None,
    registration: str,
    technology: str | None,
    thresholds_by_technology: Mapping[
        str, Mapping[str, Sequence[float]]
    ] | None = None,
) -> dict[str, str]:
    """Classify signal conservatively using CPSI RAT-specific thresholds."""
    thresholds_by_technology = (
        thresholds_by_technology or DEFAULT_SIGNAL_QUALITY_THRESHOLDS
    )
    rat = normalize_signal_technology(technology)
    if rat is None:
        rat_thresholds = DEFAULT_SIGNAL_QUALITY_THRESHOLDS["NB-IoT"]
    else:
        rat_thresholds = thresholds_by_technology.get(
            rat, DEFAULT_SIGNAL_QUALITY_THRESHOLDS[rat]
        )
    measurements = {"rsrp": rsrp, "rsrq": rsrq, "sinr": sinr}
    individual = {
        metric: classify_signal_metric(
            metric, value, rat_thresholds.get(metric)
        )
        for metric, value in measurements.items()
    }
    if registration in {"No registrado", "Buscando red", "Registro rechazado"}:
        overall = "Sin servicio"
    else:
        available = [
            quality
            for quality in individual.values()
            if quality != "Desconocida"
        ]
        overall = (
            min(available, key=_SIGNAL_QUALITY_ORDER.__getitem__)
            if available
            else "Desconocida"
        )
    available_count = sum(
        quality != "Desconocida" for quality in individual.values()
    )
    detail = (
        "diagnóstico parcial"
        if 0 < available_count < len(individual)
        else "todas las métricas disponibles"
        if available_count
        else "sin métricas válidas"
    )
    missing = [
        metric.upper()
        for metric, quality in individual.items()
        if quality == "Desconocida"
    ]
    if missing:
        detail += f"; N/D: {', '.join(missing)}"
    return {
        "signal_quality": f"{overall} ({detail})",
        "rsrp_quality": individual["rsrp"],
        "rsrq_quality": individual["rsrq"],
        "sinr_quality": individual["sinr"],
    }


def classify_nbiot_signal(
    rsrp: str | float | None,
    rsrq: str | float | None,
    sinr: str | float | None,
    registration: str,
) -> dict[str, str]:
    """Backward-compatible NB-IoT classifier."""
    result = classify_signal_quality(
        rsrp, rsrq, sinr, registration, "LTE NB-IOT"
    )
    return {
        "nbiot_quality": result["signal_quality"],
        "rsrp_quality": result["rsrp_quality"],
        "rsrq_quality": result["rsrq_quality"],
        "sinr_quality": result["sinr_quality"],
    }


def _parse_number(value: str | float | None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)", value)
    return float(match.group(1)) if match else None


def _is_number(value: str) -> bool:
    return _parse_number(value) is not None


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _number_with_unit(value: str, unit: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return "N/D"
    return f"{number:g}{unit}"


def _cpsi_unavailable() -> dict[str, str]:
    return {
        "technology": "N/D",
        "cpsi_state": "N/D",
        "band": "N/D",
        "cell": "N/D",
        "cpsi_mcc_mnc": "N/D",
        "rssi_measured": "N/D",
        "rsrp": "N/D",
        "rsrq": "N/D",
        "rssnr": "N/D",
        "sinr": "N/D",
    }


def _first_csv_fields(lines: list[bytes], prefix: bytes) -> list[str]:
    for line in lines:
        fields = _csv_fields(line, prefix)
        if fields is not None:
            return fields
    return []


def _csv_fields(line: bytes, prefix: bytes) -> list[str] | None:
    if not line.upper().startswith(prefix.upper()):
        return None
    content = line[len(prefix) :].decode("ascii", errors="replace")
    try:
        return next(csv.reader([content], skipinitialspace=True))
    except (csv.Error, StopIteration):
        return []


def _non_echo_lines(lines: list[bytes], command: bytes) -> list[str]:
    result = []
    for line in lines:
        if not line or line.upper() in {command.upper(), b"OK", b"ERROR"}:
            continue
        if line.startswith(b"+") or re.fullmatch(rb"\d+", line):
            continue
        result.append(line.decode("ascii", errors="replace"))
    return result
