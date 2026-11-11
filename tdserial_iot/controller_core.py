"""Transport and persistent local records for 1NCE Device Controller."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://api.1nce.com/management-api"
COMMANDS = frozenset(
    ("led_on", "led_off", "led_toggle", "buzzer", "telemetry", "gps", "status", "reboot")
)
MAX_ID = 2_147_483_647
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TdSerialIoT"


class ControllerError(Exception):
    """Error without credentials or server-provided sensitive content."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_payload(command: str, command_id: int, token: str, duration: int = 500) -> str:
    if command not in COMMANDS:
        raise ValueError("Comando no permitido.")
    if not isinstance(command_id, int) or isinstance(command_id, bool) or not 1 <= command_id <= MAX_ID:
        raise ValueError("ID de comando fuera del rango permitido.")
    if len(token.encode("utf-8")) < 16 or token == "CAMBIAR_CLAVE_ANTES_DE_USAR":
        raise ValueError("El token del firmware debe tener al menos 16 bytes y no ser el valor de ejemplo.")
    data: dict[str, Any] = {"cmd": command, "id": command_id}
    if command == "buzzer":
        if not isinstance(duration, int) or isinstance(duration, bool) or not 50 <= duration <= 2000:
            raise ValueError("La duración del buzzer debe estar entre 50 y 2000 ms.")
        data["duration_ms"] = duration
    elif command == "reboot":
        data["confirm"] = "REBOOT"
    data["token"] = token
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 400:
        raise ValueError("El payload supera el límite de 400 bytes del firmware.")
    return payload


class ControllerStore:
    """SQLite stores metadata only; secrets never enter the database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_DIR / "device-controller.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    iccid TEXT PRIMARY KEY, name TEXT NOT NULL, model TEXT NOT NULL,
                    port INTEGER NOT NULL, notes TEXT NOT NULL, next_id INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    record_id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL,
                    iccid TEXT NOT NULL, name TEXT NOT NULL, command TEXT NOT NULL,
                    command_id INTEGER NOT NULL, action_id TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL, api_status TEXT NOT NULL,
                    execution_status TEXT NOT NULL DEFAULT 'Sin confirmar'
                );
                """
            )

    def devices(self) -> list[tuple[Any, ...]]:
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT iccid,name,model,port,notes,next_id FROM devices WHERE name<>'' ORDER BY name,iccid"
            ).fetchall()

    def save_device(self, iccid: str, name: str, model: str, port: int, notes: str) -> None:
        if not iccid.isascii() or not iccid.isdigit() or not 18 <= len(iccid) <= 22:
            raise ValueError("El ICCID / Device ID debe tener entre 18 y 22 dígitos.")
        if not name.strip() or not 1 <= port <= 65535:
            raise ValueError("Indica un nombre y un puerto UDP válido.")
        with sqlite3.connect(self.path) as db:
            db.execute(
                """INSERT INTO devices VALUES(?,?,?,?,?,1)
                ON CONFLICT(iccid) DO UPDATE SET name=excluded.name,model=excluded.model,
                port=excluded.port,notes=excluded.notes""",
                (iccid, name.strip(), model, port, notes),
            )

    def delete_device(self, iccid: str) -> None:
        # Keep the counter to prevent reuse when the same device is added again.
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE devices SET name='',model='',notes='' WHERE iccid=?", (iccid,))

    def next_id(self, iccid: str) -> int:
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT next_id FROM devices WHERE iccid=?", (iccid,)).fetchone()
            if row is None or row[0] > MAX_ID:
                raise ValueError("Dispositivo no disponible o contador de ID agotado.")
            db.execute("UPDATE devices SET next_id=? WHERE iccid=?", (row[0] + 1, iccid))
            return int(row[0])

    def reserve_id(self, iccid: str, command_id: int, reserved_id: int | None = None) -> None:
        if not isinstance(command_id, int) or isinstance(command_id, bool) or not 1 <= command_id <= MAX_ID:
            raise ValueError("ID de comando fuera del rango permitido.")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT next_id FROM devices WHERE iccid=? AND name<>''", (iccid,)).fetchone()
            if row is None:
                raise ValueError("Dispositivo no disponible.")
            if command_id == reserved_id:
                return
            if command_id < row[0]:
                raise ValueError("El ID ya se ha reservado; utiliza uno nuevo para evitar duplicados.")
            db.execute("UPDATE devices SET next_id=? WHERE iccid=?", (command_id + 1, iccid))

    def record(self, created: str, iccid: str, name: str, command: str, cmd_id: int, mode: str) -> int:
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "INSERT INTO history(created,iccid,name,command,command_id,mode,api_status) "
                "VALUES(?,?,?,?,?,?,?)",
                (created, iccid, name, command, cmd_id, mode, "Solicitud enviada"),
            )
            return int(cursor.lastrowid or 0)

    def update_record(self, record_id: int, status: str, action_id: str = "") -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE history SET api_status=?,action_id=CASE WHEN ?='' THEN action_id ELSE ? END "
                "WHERE record_id=?", (status, action_id, action_id, record_id)
            )

    def history(self) -> list[tuple[Any, ...]]:
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT record_id,created,iccid,name,command,command_id,action_id,mode,"
                "api_status,execution_status FROM history ORDER BY record_id DESC"
            ).fetchall()

    def get_setting(self, key: str, default: str) -> str:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return str(row[0]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class ControllerClient:
    def __init__(self, base_url: str, client_id: str, secret: str, timeout: int = 30) -> None:
        base_url = base_url.strip().rstrip("/")
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("La URL de API debe ser HTTPS y no contener credenciales ni parámetros.")
        if not client_id.strip() or not secret or not 1 <= timeout <= 120:
            raise ValueError("Indica las credenciales y un timeout entre 1 y 120 segundos.")
        self.base_url, self.client_id, self.secret, self.timeout = base_url, client_id, secret, timeout
        self._token = ""
        self._expires = 0.0
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )

    def _request(self, method: str, path: str, body: dict[str, Any] | None, auth: str) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                data = response.read()
        except urllib.error.HTTPError as exc:
            explanation = {
                400: "Solicitud no válida",
                401: "Credenciales o sesión rechazadas",
                403: "Permisos insuficientes o condiciones de la cuenta pendientes",
                404: "Recurso no encontrado",
                409: "Conflicto de solicitud",
                429: "Límite de solicitudes alcanzado",
                500: "Error del servidor 1NCE",
                502: "Puerta de enlace de 1NCE no disponible",
                503: "Servicio 1NCE no disponible",
                504: "Tiempo de espera agotado en 1NCE",
            }.get(exc.code, "1NCE rechazó la petición")
            if exc.code == 404:
                if path == "/oauth/token":
                    explanation = (
                        "No se encontró el endpoint de autenticación; "
                        "comprueba la URL base de la API"
                    )
                elif method == "POST" and path.endswith("/actions/UDP"):
                    explanation = (
                        "1NCE no encontró el recurso solicitado. Comprueba la URL base "
                        "y que el Device ID del dispositivo seleccionado coincida "
                        "exactamente con el de Device Inspector para esta cuenta; "
                        "puede diferir del ICCID leído por el módem"
                    )
                elif method == "GET" and path.startswith("/v1/integrate/devices/actions/requests/"):
                    explanation = (
                        "1NCE no encontró el recurso solicitado. Comprueba la URL base "
                        "y el ID de acción de esta cuenta"
                    )
            raise ControllerError(
                f"{explanation} (HTTP {exc.code}).", status_code=exc.code
            ) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.gaierror):
                raise ControllerError("No se pudo resolver el servidor 1NCE (DNS).") from None
            raise ControllerError(
                "Error de red o timeout. El resultado de un envío puede ser desconocido."
            ) from None
        except (TimeoutError, OSError):
            raise ControllerError("Error de red o timeout. El resultado de un envío puede ser desconocido.") from None
        try:
            result = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ControllerError("1NCE devolvió JSON no válido; el resultado del envío puede ser desconocido.") from None
        if not isinstance(result, dict):
            raise ControllerError("1NCE devolvió una respuesta inesperada.")
        return status, result

    def authenticate(self, force: bool = False) -> None:
        if not force and self._token and time.monotonic() < self._expires:
            return
        credentials = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode("ascii")
        status, response = self._request("POST", "/oauth/token", {"grant_type": "client_credentials"}, "Basic " + credentials)
        token = response.get("access_token")
        expires = response.get("expires_in")
        try:
            lifetime = int(expires) if expires is not None else 0
        except (ValueError, TypeError):
            raise ControllerError("La caducidad del token de 1NCE no es válida.") from None
        if status != 200 or not isinstance(token, str) or not token or lifetime < 0:
            raise ControllerError("La respuesta de autenticación no contiene un token válido.")
        self._token = token
        self._expires = time.monotonic() + max(0, lifetime - 30)

    def action(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        self.authenticate()
        try:
            return self._request(method, path, body, "Bearer " + self._token)
        except ControllerError as exc:
            if exc.status_code != 401:
                raise
        self.authenticate(force=True)
        return self._request(method, path, body, "Bearer " + self._token)

    def send(self, iccid: str, port: int, mode: str, payload: str) -> str:
        if not iccid.isascii() or not iccid.isdigit() or not 18 <= len(iccid) <= 22:
            raise ValueError("El ICCID / Device ID debe tener entre 18 y 22 dígitos.")
        if not 1 <= port <= 65535 or mode not in {"SEND_NOW", "SEND_WHEN_ACTIVE"}:
            raise ValueError("Puerto o modo de envío no válido.")
        if len(payload.encode("utf-8")) > 400:
            raise ValueError("El payload supera el límite del firmware.")
        status, response = self.action(
            "POST",
            f"/v1/integrate/devices/{iccid}/actions/UDP",
            {"payload": payload, "payloadType": "STRING", "port": port, "requestMode": mode},
        )
        action_id = response.get("id")
        if status != 202 or not isinstance(action_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", action_id):
            raise ControllerError("Respuesta inesperada al enviar la solicitud; el resultado puede ser desconocido.")
        return action_id

    def status(self, action_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", action_id):
            raise ValueError("Introduce un ID de acción válido.")
        status, response = self.action(
            "GET", "/v1/integrate/devices/actions/requests/" + urllib.parse.quote(action_id, safe="")
        )
        if status != 200 or not isinstance(response.get("status"), str):
            raise ControllerError("Respuesta de estado de acción inesperada.")
        return response
