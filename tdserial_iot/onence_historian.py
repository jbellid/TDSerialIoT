"""Read-only Device Inspector Historian client and payload interpretation."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tdserial_iot.onence_api import (
    HTTP_TIMEOUT_SECONDS,
    HISTORIAN_DEVICE_HISTORY_PATH,
    MANAGEMENT_API_BASE_URL,
    OneNCEAuthenticator,
    safe_error_body,
    safe_response_headers,
)
from tdserial_iot.iot_operations import TransmissionRecord

logger = logging.getLogger(__name__)

HISTORIAN_MAX_PAGE_SIZE = 25
HISTORIAN_RETENTION_DAYS = 7
HISTORIAN_PAGE_URL = f"{MANAGEMENT_API_BASE_URL}{HISTORIAN_DEVICE_HISTORY_PATH}"
HISTORIAN_ALL_HISTORY_URL = (
    f"{MANAGEMENT_API_BASE_URL}/v1/inspect/devices/history"
)
INSPECT_DEVICES_URL = f"{MANAGEMENT_API_BASE_URL}/v1/inspect/devices"
SIM_METADATA_URL = f"{MANAGEMENT_API_BASE_URL}/v1/sims"
DEVICE_LIST_PAGE_SIZE = 50
DEVICE_LIST_MAX_PAGES = 100
DEVICE_LIST_MAX_ITEMS = DEVICE_LIST_PAGE_SIZE * DEVICE_LIST_MAX_PAGES


class OneNCEHistoryError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True)
class CloudMessage:
    device_id: str
    iccid: str
    timestamp: datetime | None
    timestamp_original: str
    protocol: str
    payload_original: str
    payload_bytes: bytes | None
    payload_json: Any | None
    payload_json_valid: bool
    content_type: str
    decode_status: str
    message_id: str = ""

    @property
    def has_timezone(self) -> bool:
        return self.timestamp is not None and self.timestamp.tzinfo is not None


@dataclass(frozen=True)
class HistoryPage:
    messages: tuple[CloudMessage, ...]
    next_token: str | None


@dataclass(frozen=True)
class OneNCEDevice:
    iccid: str
    label: str = ""


@dataclass(frozen=True)
class TransmissionCorrelation:
    local_index: int
    status: str
    cloud_index: int | None = None


def parse_history_response(value: Any) -> HistoryPage:
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise OneNCEHistoryError("La respuesta de Historian no contiene una lista items.")
    next_token = value.get("nextToken")
    if next_token is not None and (not isinstance(next_token, str) or not next_token):
        raise OneNCEHistoryError("Historian devolvió un nextToken no válido.")

    messages: list[CloudMessage] = []
    for index, item in enumerate(value["items"]):
        if not isinstance(item, dict):
            raise OneNCEHistoryError(
                f"El mensaje {index + 1} de Historian no tiene un formato válido."
            )
        messages.append(parse_cloud_message(item))
    return HistoryPage(sort_messages_newest_first(messages), next_token)


def parse_cloud_message(value: dict[str, Any]) -> CloudMessage:
    device_id = _string_field(value, "deviceId")
    iccid = _string_field(value, "iccid")
    timestamp_original = _string_field(value, "time")
    protocol = _string_field(value, "protocol").upper()
    message_id = (
        _string_field(value, "messageId")
        or _string_field(value, "eventId")
        or _string_field(value, "id")
    )
    payload = value.get("payload", "")
    if isinstance(payload, str):
        payload_original = payload
    elif payload is None:
        payload_original = ""
    else:
        payload_original = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    timestamp = _parse_timestamp(timestamp_original)
    payload_bytes: bytes | None = None
    payload_json: Any | None = None
    payload_json_valid = False
    content_type = "Texto"
    decode_status = "Texto UTF-8 original"

    try:
        payload_bytes = payload_original.encode("utf-8")
    except UnicodeEncodeError:
        payload_bytes = None
    else:
        try:
            payload_json = json.loads(payload_original)
        except json.JSONDecodeError:
            pass
        else:
            content_type = "JSON"
            decode_status = "JSON válido en el payload original"
            payload_json_valid = True

    if not payload_json_valid and protocol in {"COAP", "LWM2M"}:
        decoded = decode_base64(payload_original)
        if decoded is not None:
            payload_bytes = decoded
            try:
                payload_text = decoded.decode("utf-8")
            except UnicodeDecodeError:
                content_type = "Binario (Base64)"
                decode_status = "Base64 válido; bytes no interpretables como UTF-8"
            else:
                try:
                    payload_json = json.loads(payload_text)
                except json.JSONDecodeError:
                    content_type = "Texto (Base64)"
                    decode_status = "Base64 válido; contenido UTF-8"
                else:
                    content_type = "JSON (Base64)"
                    decode_status = "Base64 válido; contenido JSON"
                    payload_json_valid = True

    if payload_bytes is None:
        content_type = "Texto"
        decode_status = "Payload original no codificable como UTF-8"
    return CloudMessage(
        device_id=device_id,
        iccid=iccid,
        timestamp=timestamp,
        timestamp_original=timestamp_original,
        protocol=protocol or "N/D",
        payload_original=payload_original,
        payload_bytes=payload_bytes,
        payload_json=payload_json,
        payload_json_valid=payload_json_valid,
        content_type=content_type,
        decode_status=decode_status,
        message_id=message_id,
    )


def decode_base64(value: str) -> bytes | None:
    compact = "".join(value.split())
    if not compact or len(compact) % 4:
        return None
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return None


def render_payload(message: CloudMessage, representation: str) -> str:
    if representation == "Original":
        return message.payload_original
    if representation == "JSON":
        if message.payload_json_valid:
            return json.dumps(message.payload_json, ensure_ascii=False, indent=2)
        decoded = decode_base64(message.payload_original)
        if decoded is not None:
            try:
                decoded_json = json.loads(decoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            else:
                return json.dumps(decoded_json, ensure_ascii=False, indent=2)
        raise ValueError("Este mensaje no contiene JSON decodificable.")
    if representation == "Texto":
        if message.payload_bytes is None:
            return message.payload_original
        return message.payload_bytes.decode("utf-8", errors="replace")
    if representation == "Base64 decodificado":
        decoded = decode_base64(message.payload_original)
        if decoded is None:
            raise ValueError("El payload original no es Base64 válido.")
        return decoded.decode("utf-8", errors="replace")
    if representation == "HEX":
        decoded = decode_base64(message.payload_original)
        raw = decoded if decoded is not None else message.payload_original.encode("utf-8")
        return raw.hex(" ").upper()
    raise ValueError(f"Representación de payload no admitida: {representation}")


def correlate_transmissions(
    local_records: list[TransmissionRecord],
    cloud_messages: list[CloudMessage],
    device_id: str,
    *,
    history_queried: bool,
    approximate_window_seconds: int = 300,
) -> tuple[TransmissionCorrelation, ...]:
    correlations: list[TransmissionCorrelation] = []
    target_id = device_id.strip()
    for local_index, record in enumerate(local_records):
        if record.payload_size <= 0 or record.payload is None:
            continue
        if not record.successful:
            correlations.append(
                TransmissionCorrelation(local_index, "No enviado al módem")
            )
            continue

        candidates: list[int] = []
        if record.sequence_id is not None and record.device_id.strip() == target_id:
            candidates = [
                index
                for index, message in enumerate(cloud_messages)
                if _same_device(message, target_id)
                and _message_sequence(message) == record.sequence_id
            ]
        if not candidates:
            candidates = [
                index
                for index, message in enumerate(cloud_messages)
                if _same_device(message, target_id)
                and message.protocol == "UDP"
                and message.payload_bytes == record.payload
                and _timestamps_within(
                    record.timestamp,
                    message.timestamp,
                    approximate_window_seconds,
                )
            ]

        if len(candidates) == 1:
            correlations.append(
                TransmissionCorrelation(
                    local_index, "Confirmado en 1NCE", candidates[0]
                )
            )
        elif len(candidates) > 1:
            correlations.append(
                TransmissionCorrelation(
                    local_index, "Correspondencia ambigua en 1NCE"
                )
            )
        elif history_queried:
            correlations.append(
                TransmissionCorrelation(
                    local_index, "No encontrado en el periodo consultado"
                )
            )
        else:
            correlations.append(
                TransmissionCorrelation(
                    local_index, "Pendiente de confirmación en 1NCE"
                )
            )
    return tuple(correlations)


class OneNCEHistorianClient:
    def __init__(self, authenticator: OneNCEAuthenticator) -> None:
        self._authenticator = authenticator

    def get_all_history_page(
        self,
        *,
        page_size: int | None = 10,
        protocol: str | None = "UDP",
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
        next_token: str | None = None,
    ) -> HistoryPage:
        query = self._history_query(
            page_size=page_size,
            protocol=protocol,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            next_token=next_token,
        )
        return self._get_history_page(HISTORIAN_ALL_HISTORY_URL, query)

    def get_history_page(
        self,
        device_id: str,
        *,
        page_size: int | None = 10,
        protocol: str | None = "UDP",
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
        next_token: str | None = None,
    ) -> HistoryPage:
        identifier = device_id.strip()
        if not identifier:
            raise ValueError("Introduce el ICCID / Device ID.")
        query = self._history_query(
            page_size=page_size,
            protocol=protocol,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            next_token=next_token,
        )
        url = HISTORIAN_PAGE_URL.format(
            device_id=urllib.parse.quote(identifier, safe="")
        )
        return self._get_history_page(url, query)

    def _history_query(
        self,
        *,
        page_size: int | None,
        protocol: str | None,
        start_datetime: datetime | None,
        end_datetime: datetime | None,
        next_token: str | None,
    ) -> dict[str, str]:
        if page_size is not None and not 1 <= page_size <= HISTORIAN_MAX_PAGE_SIZE:
            raise ValueError(
                f"El tamaño de página debe estar entre 1 y {HISTORIAN_MAX_PAGE_SIZE}."
            )
        query: dict[str, str] = (
            {"pageSize": str(page_size)} if page_size is not None else {}
        )
        if protocol and protocol.upper() != "TODOS":
            normalized_protocol = protocol.upper()
            if normalized_protocol not in {"UDP", "COAP", "LWM2M"}:
                raise ValueError("Protocolo de Historian no válido.")
            query["protocol"] = normalized_protocol
        if start_datetime is not None:
            query["startDateTime"] = _format_query_timestamp(start_datetime)
        if end_datetime is not None:
            query["endDateTime"] = _format_query_timestamp(end_datetime)
        if next_token is not None:
            query["nextToken"] = next_token
        return query

    def _get_history_page(
        self, url: str, query: dict[str, str]
    ) -> HistoryPage:
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = self._request_body(url, service="Historian")

        try:
            response_data = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OneNCEHistoryError(
                "Historian devolvió una respuesta JSON no válida."
            ) from None
        return parse_history_response(response_data)

    def get_device_iccids(self) -> tuple[str, ...]:
        return self._get_inspect_devices()

    def get_sim_labels(self) -> dict[str, str]:
        return self._get_sim_labels()

    def _get_inspect_devices(self) -> tuple[str, ...]:
        device_ids: list[str] = []
        seen_ids: set[str] = set()
        seen_tokens: set[str] = set()
        next_token: str | None = None
        page_number = 1

        for _ in range(DEVICE_LIST_MAX_PAGES):
            query = {"pageSize": str(DEVICE_LIST_PAGE_SIZE)}
            if next_token is not None:
                query["nextToken"] = next_token
            elif page_number > 1:
                query["page"] = str(page_number)
            body = self._request_body(
                f"{INSPECT_DEVICES_URL}?{urllib.parse.urlencode(query)}",
                service="Device Inspector",
            )
            response = self._decode_json(body, "Device Inspector")
            previous_count = len(device_ids)
            items, next_token = _parse_collection(response, ("items", "devices"))
            for item in items:
                iccid = _device_iccid(item)
                if iccid and iccid not in seen_ids:
                    device_ids.append(iccid)
                    seen_ids.add(iccid)
                    if len(device_ids) > DEVICE_LIST_MAX_ITEMS:
                        raise OneNCEHistoryError(
                            "La lista de dispositivos alcanzó el límite de "
                            f"{DEVICE_LIST_MAX_ITEMS}; refina la consulta."
                        )
            if next_token:
                if next_token in seen_tokens:
                    raise OneNCEHistoryError(
                        "Device Inspector repitió un nextToken; "
                        "se detuvo la paginación."
                    )
                seen_tokens.add(next_token)
                continue
            total = _collection_total(response)
            if total is not None and len(device_ids) >= total:
                return tuple(device_ids)
            if total is None and len(items) < DEVICE_LIST_PAGE_SIZE:
                return tuple(device_ids)
            if len(device_ids) == previous_count:
                raise OneNCEHistoryError(
                    "Device Inspector no avanzó al paginar los dispositivos."
                )
            page_number += 1
        raise OneNCEHistoryError(
            f"La lista de dispositivos superó el límite de "
            f"{DEVICE_LIST_MAX_PAGES} páginas."
        )

    def _get_sim_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        seen_iccids: set[str] = set()
        for page_number in range(1, DEVICE_LIST_MAX_PAGES + 1):
            query = urllib.parse.urlencode(
                {"page": str(page_number), "pageSize": str(DEVICE_LIST_PAGE_SIZE)}
            )
            body = self._request_body(
                f"{SIM_METADATA_URL}?{query}", service="metadatos SIM"
            )
            response = self._decode_json(body, "metadatos SIM")
            items, _ = _parse_collection(response, ("items", "sims", "results"))
            for item in items:
                if not isinstance(item, dict):
                    continue
                iccid = _device_iccid(item)
                label = _device_label(item)
                if iccid and iccid not in seen_iccids:
                    seen_iccids.add(iccid)
                    if label:
                        labels[iccid] = label
            total = _collection_total(response)
            if (
                page_number * DEVICE_LIST_PAGE_SIZE >= total
                if total is not None
                else len(items) < DEVICE_LIST_PAGE_SIZE
            ):
                return labels
        raise OneNCEHistoryError(
            f"Los metadatos SIM superaron el límite de "
            f"{DEVICE_LIST_MAX_PAGES} páginas."
        )

    @staticmethod
    def _decode_json(body: bytes, source: str) -> Any:
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OneNCEHistoryError(
                f"1NCE devolvió una respuesta JSON no válida en {source}."
            ) from None

    def _request_body(self, url: str, *, service: str) -> bytes:
        for attempt in range(2):
            token = self._authenticator.get_access_token(
                force_refresh=attempt == 1
            )
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token.value}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            query_parameters = dict(
                urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
            )
            logger.info(
                "1NCE HTTP request method=GET service=%s url=%s params=%s "
                "headers=%s attempt=%d",
                service,
                url,
                query_parameters,
                {"Accept": "application/json"},
                attempt + 1,
            )
            started_at = time.perf_counter()
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=HTTP_TIMEOUT_SECONDS,
                    context=ssl.create_default_context(),
                ) as response:
                    response_body = response.read()
                    status_code = getattr(response, "status", None) or getattr(
                        response, "getcode", lambda: 200
                    )()
                    response_headers = safe_response_headers(
                        getattr(response, "headers", None)
                    )
                logger.info(
                    "1NCE HTTP response method=GET service=%s url=%s status=%s "
                    "elapsed_ms=%d headers=%s",
                    service,
                    url,
                    status_code,
                    int((time.perf_counter() - started_at) * 1000),
                    response_headers,
                )
                return response_body
            except urllib.error.HTTPError as exc:
                try:
                    error_body = safe_error_body(
                        exc.read(),
                        token.value,
                        f"Bearer {token.value}",
                    )
                except (OSError, ValueError):
                    error_body = "[no se pudo leer el cuerpo de error]"
                logger.warning(
                    "1NCE HTTP response method=GET service=%s url=%s status=%s "
                    "elapsed_ms=%d headers=%s error_body=%s",
                    service,
                    url,
                    exc.code,
                    int((time.perf_counter() - started_at) * 1000),
                    safe_response_headers(exc.headers),
                    error_body,
                )
                if exc.code == 401 and attempt == 0:
                    continue
                retry_after = (
                    exc.headers.get("Retry-After") if exc.headers is not None else None
                )
                raise OneNCEHistoryError(
                    _http_error_message(exc.code, retry_after, service=service),
                    status_code=exc.code,
                    retry_after=retry_after,
                ) from None
            except (TimeoutError, urllib.error.URLError, OSError):
                logger.warning(
                    "1NCE HTTP failure method=GET url=%s service=%s "
                    "elapsed_ms=%d error=network",
                    url,
                    service,
                    int((time.perf_counter() - started_at) * 1000),
                )
                raise OneNCEHistoryError(
                    f"No se pudo conectar con {service}. Comprueba la red e "
                    "inténtalo de nuevo."
                ) from None
        raise OneNCEHistoryError("No se pudo renovar la sesión de Historian.")


def _parse_collection(
    value: Any, item_keys: tuple[str, ...]
) -> tuple[list[Any], str | None]:
    if isinstance(value, list):
        return value, None
    if not isinstance(value, dict):
        raise OneNCEHistoryError("1NCE devolvió una lista de dispositivos no válida.")
    items: Any = None
    for key in item_keys:
        if isinstance(value.get(key), list):
            items = value[key]
            break
    if items is None:
        nested = value.get("data")
        if isinstance(nested, list):
            return nested, None
        if isinstance(nested, dict):
            return _parse_collection(nested, item_keys)
        raise OneNCEHistoryError(
            "La respuesta de 1NCE no contiene una lista de dispositivos."
        )
    token = value.get("nextToken", value.get("next_token"))
    if token is not None and (not isinstance(token, str) or not token):
        raise OneNCEHistoryError("1NCE devolvió un nextToken no válido.")
    return items, token


def _collection_total(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in ("total", "totalCount", "totalItems", "count"):
        total = value.get(key)
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            return total
    return None


def _device_iccid(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("iccid", "ICCID", "deviceId", "device_id", "id"):
        identifier = value.get(key)
        if isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
            normalized = str(identifier).strip()
            if normalized:
                return normalized
    return ""


def _device_label(value: dict[str, Any]) -> str:
    for key in ("label", "LABEL", "simLabel", "sim_label"):
        label = value.get(key)
        if isinstance(label, str) and label.strip():
            return label.strip()
    return ""


def _string_field(value: dict[str, Any], name: str) -> str:
    item = value.get(name, "")
    return item if isinstance(item, str) else str(item)


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else parsed
    except ValueError:
        return None


def sort_messages_newest_first(messages: list[CloudMessage]) -> tuple[CloudMessage, ...]:
    dated = [message for message in messages if message.timestamp is not None]
    if not dated:
        return tuple(messages)
    aware = [
        message.timestamp.tzinfo is not None
        for message in dated
        if message.timestamp is not None
    ]
    if all(aware):
        order_key = lambda item: item.timestamp
    elif not any(aware):
        order_key = lambda item: item.timestamp
    else:
        return tuple(messages)
    ordered = sorted(dated, key=order_key, reverse=True)
    undated = [message for message in messages if message.timestamp is None]
    return tuple(ordered + undated)


def _format_query_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError(
            "Las fechas de consulta deben incluir la zona horaria local."
        )
    return value.isoformat(timespec="milliseconds")


def _same_device(message: CloudMessage, device_id: str) -> bool:
    return bool(device_id) and (message.device_id == device_id or message.iccid == device_id)


def _message_sequence(message: CloudMessage) -> str | None:
    if not isinstance(message.payload_json, dict):
        return None
    sequence = message.payload_json.get("seq")
    if isinstance(sequence, (str, int)) and not isinstance(sequence, bool):
        return str(sequence)
    return None


def _timestamps_within(
    local_timestamp: datetime, cloud_timestamp: datetime | None, seconds: int
) -> bool:
    if cloud_timestamp is None:
        return False
    local_aware = local_timestamp.tzinfo is not None
    cloud_aware = cloud_timestamp.tzinfo is not None
    if local_aware != cloud_aware:
        return False
    difference = abs((local_timestamp - cloud_timestamp).total_seconds())
    return difference <= seconds


def _http_error_message(
    status_code: int,
    retry_after: str | None,
    *,
    service: str = "Historian",
) -> str:
    if status_code == 400:
        return f"{service} rechazó los parámetros de la solicitud (HTTP 400)."
    if status_code == 401:
        return f"1NCE rechazó el token de acceso al consultar {service} (HTTP 401)."
    if status_code == 403:
        return (
            "1NCE rechazó la solicitud (HTTP 403). Comprueba el acceso al endpoint "
            "y consulta el detalle de respuesta en logs/tdserial-iot.log."
        )
    if status_code == 404:
        return f"No se encontró el recurso de {service} (HTTP 404)."
    if status_code == 429:
        suffix = f" Reintenta después de {retry_after}." if retry_after else ""
        return (
            f"1NCE limitó temporalmente las solicitudes a {service} (HTTP 429)."
            + suffix
        )
    if status_code >= 500:
        return f"El servicio {service} de 1NCE no está disponible temporalmente (HTTP 5xx)."
    return f"1NCE rechazó la consulta a {service} (HTTP {status_code})."
