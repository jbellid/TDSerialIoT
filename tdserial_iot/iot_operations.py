"""SIM7080G UDP payload validation, command construction, and response parsing."""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tdserial_iot.command_queue import AtCommand, InteractionPlan, make_at_command

UDP_MAX_PAYLOAD_BYTES = 1460
CASEND_MIN_INPUT_TIME_MS = 100
CASEND_MAX_INPUT_TIME_MS = 10_000
DEFAULT_CASEND_INPUT_TIME_MS = 5000
MAX_APN_LENGTH = 150
MAX_HOST_LENGTH = 64

PayloadFormat = Literal["ASCII / UTF-8", "JSON", "HEX", "Binario de archivo"]


@dataclass(frozen=True)
class ApplicationContext:
    context_id: int
    status: int
    address: str

    @property
    def active(self) -> bool:
        return self.status == 1


@dataclass(frozen=True)
class ApplicationConfig:
    context_id: int
    ip_type: int
    apn: str
    username: str
    password: str
    authentication: int


@dataclass(frozen=True)
class TransmissionRecord:
    timestamp: datetime
    protocol: str
    host: str
    port: int
    socket_id: int
    payload_size: int
    payload_type: str
    result: str
    duration_ms: int


def encode_payload(
    payload_format: PayloadFormat, text: str = "", file_data: bytes | None = None
) -> bytes:
    """Encode user input without adding line endings or normalizing JSON."""
    if payload_format == "Binario de archivo":
        if file_data is None:
            raise ValueError("Selecciona un archivo binario.")
        return file_data
    if payload_format == "HEX":
        compact = re.sub(r"\s+", "", text)
        if not compact or len(compact) % 2:
            raise ValueError("La entrada HEX debe contener pares de dígitos.")
        if re.fullmatch(r"[0-9a-fA-F]+", compact) is None:
            raise ValueError("La entrada HEX solo admite dígitos hexadecimales y espacios.")
        return bytes.fromhex(compact)
    if payload_format == "JSON":
        try:
            json.loads(text, parse_constant=_reject_nonstandard_json_constant)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"JSON inválido en línea {exc.lineno}, columna {exc.colno}: {exc.msg}"
            ) from exc
        except ValueError as exc:
            raise ValueError(f"JSON inválido: {exc}") from exc
        return text.encode("utf-8")
    if payload_format == "ASCII / UTF-8":
        return text.encode("utf-8")
    raise ValueError(f"Formato de payload no soportado: {payload_format}")


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"{value} no es un valor JSON estándar.")


def validate_apn(apn: str) -> str:
    value = apn.strip()
    if (
        not value
        or len(value) > MAX_APN_LENGTH
        or re.fullmatch(r"[A-Za-z0-9.-]+", value) is None
        or value.startswith(("-", "."))
        or value.endswith(("-", "."))
    ):
        raise ValueError("El APN debe contener únicamente letras, números, puntos o guiones.")
    return value


def validate_host(host: str) -> str:
    value = host.strip()
    if not value or len(value) > MAX_HOST_LENGTH:
        raise ValueError(f"El host debe tener entre 1 y {MAX_HOST_LENGTH} caracteres.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if (
            re.fullmatch(r"[A-Za-z0-9.-]+", value) is None
            or value.startswith(("-", "."))
            or value.endswith(("-", "."))
            or ".." in value
        ):
            raise ValueError("El host debe ser una IP o un nombre DNS válido.")
    return value


def build_cn_config_command(context_id: int, apn: str) -> bytes:
    if not 0 <= context_id <= 3:
        raise ValueError("El contexto de aplicaciones debe estar entre 0 y 3.")
    safe_apn = validate_apn(apn)
    return f'AT+CNCFG={context_id},1,"{safe_apn}"\r'.encode("ascii")


def build_cn_activate_command(context_id: int) -> bytes:
    if not 0 <= context_id <= 3:
        raise ValueError("El contexto de aplicaciones debe estar entre 0 y 3.")
    return f"AT+CNACT={context_id},1\r".encode("ascii")


def build_open_socket_command(
    socket_id: int, context_id: int, host: str, port: int, timeout_ms: int
) -> AtCommand:
    if not 0 <= socket_id <= 12:
        raise ValueError("El identificador de socket debe estar entre 0 y 12.")
    if not 0 <= context_id <= 3:
        raise ValueError("El contexto de aplicaciones debe estar entre 0 y 3.")
    if not 1 <= port <= 65535:
        raise ValueError("El puerto UDP debe estar entre 1 y 65535.")
    safe_host = validate_host(host)
    command = (
        f'AT+CAOPEN={socket_id},{context_id},"UDP","{safe_host}",{port},0\r'
    ).encode("ascii")
    return make_at_command(command, timeout_ms, purpose="iot:open:open")


def build_close_socket_command(socket_id: int, timeout_ms: int) -> AtCommand:
    if not 0 <= socket_id <= 12:
        raise ValueError("El identificador de socket debe estar entre 0 y 12.")
    return make_at_command(
        f"AT+CACLOSE={socket_id}\r".encode("ascii"),
        timeout_ms,
        purpose="iot:close:close",
    )


def build_receive_command(
    socket_id: int, read_length: int, timeout_ms: int
) -> AtCommand:
    if not 0 <= socket_id <= 12:
        raise ValueError("El identificador de socket debe estar entre 0 y 12.")
    if not 1 <= read_length <= UDP_MAX_PAYLOAD_BYTES:
        raise ValueError(f"La longitud de lectura debe estar entre 1 y {UDP_MAX_PAYLOAD_BYTES} bytes.")
    return make_at_command(
        f"AT+CARECV={socket_id},{read_length}\r".encode("ascii"),
        timeout_ms,
        purpose="iot:receive:read",
    )


def build_send_command(
    socket_id: int,
    payload: bytes,
    prompt_timeout_ms: int,
    input_time_ms: int,
    response_timeout_ms: int,
) -> AtCommand:
    if not 0 <= socket_id <= 12:
        raise ValueError("El identificador de socket debe estar entre 0 y 12.")
    if not payload:
        raise ValueError("El payload no puede estar vacío.")
    if len(payload) > UDP_MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"El payload supera el máximo SIM7080G de {UDP_MAX_PAYLOAD_BYTES} bytes."
        )
    if not CASEND_MIN_INPUT_TIME_MS <= input_time_ms <= CASEND_MAX_INPUT_TIME_MS:
        raise ValueError(
            "La ventana de entrada CASEND debe estar entre "
            f"{CASEND_MIN_INPUT_TIME_MS} y {CASEND_MAX_INPUT_TIME_MS} ms."
        )
    command = f"AT+CASEND={socket_id},{len(payload)},{input_time_ms}\r".encode("ascii")
    return AtCommand(
        data=command,
        timeout_ms=prompt_timeout_ms,
        interaction=InteractionPlan(
            prompt=b">",
            payload=payload,
            response_timeout_ms=response_timeout_ms,
        ),
        purpose="iot:send:payload",
    )


def parse_application_context(
    response_lines: list[bytes], context_id: int
) -> ApplicationContext | None:
    for fields in _response_csv_fields(response_lines, b"+CNACT:"):
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        if int(fields[0]) == context_id:
            return ApplicationContext(context_id, int(fields[1]), fields[2])
    return None


def parse_application_config(
    response_lines: list[bytes], context_id: int
) -> ApplicationConfig | None:
    for fields in _response_csv_fields(response_lines, b"+CNCFG:"):
        if (
            len(fields) < 6
            or not fields[0].isdigit()
            or not fields[1].isdigit()
            or not fields[5].isdigit()
        ):
            continue
        if int(fields[0]) == context_id:
            return ApplicationConfig(
                context_id,
                int(fields[1]),
                fields[2],
                fields[3],
                fields[4],
                int(fields[5]),
            )
    return None


def parse_socket_state(response_lines: list[bytes], socket_id: int) -> int | None:
    for fields in _response_csv_fields(response_lines, b"+CASTATE:"):
        if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
            if int(fields[0]) == socket_id:
                return int(fields[1])
    return 0


def _response_csv_fields(
    response_lines: list[bytes], prefix: bytes
) -> list[list[str]]:
    result: list[list[str]] = []
    for line in response_lines:
        stripped = line.strip(b"\r\n \t")
        if not stripped.upper().startswith(prefix.upper()):
            continue
        content = stripped[len(prefix) :].decode("ascii", errors="replace")
        try:
            result.append(next(csv.reader(io.StringIO(content), skipinitialspace=True)))
        except (csv.Error, StopIteration):
            continue
    return result
