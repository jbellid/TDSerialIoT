"""Authentication primitives and secure credential storage for 1NCE OS."""

from __future__ import annotations

import base64
import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

MANAGEMENT_API_BASE_URL = "https://api.1nce.com/management-api"
OAUTH_TOKEN_URL = f"{MANAGEMENT_API_BASE_URL}/oauth/token"
HISTORIAN_DEVICE_HISTORY_PATH = "/v1/inspect/devices/{device_id}/history"
KEYRING_SERVICE_NAME = "TdSerial IoT - 1NCE OS"
KEYRING_CREDENTIALS_KEY = "api-credentials"
KEYRING_ICCID_KEY = "device-iccid"
HTTP_TIMEOUT_SECONDS = 12
SAFE_RESPONSE_HEADER_NAMES = frozenset(
    {
        "content-type",
        "date",
        "retry-after",
        "server",
        "x-correlation-id",
        "x-request-id",
    }
)


class OneNCEAuthError(Exception):
    """Safe-to-display authentication error without response bodies or secrets."""


class CredentialStoreError(Exception):
    """Secure credential storage is unavailable or failed."""


@dataclass(frozen=True)
class ApiCredentials:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class AccessToken:
    value: str
    token_type: str
    expires_in: int | None


class OneNCEAuthenticator:
    """Keep a validated API token in memory and renew it before expiry."""

    def __init__(self, credentials: ApiCredentials) -> None:
        self._credentials = credentials
        self._token: AccessToken | None = None
        self._expires_at: datetime | None = None
        self._lock = threading.Lock()

    def get_access_token(self, *, force_refresh: bool = False) -> AccessToken:
        with self._lock:
            now = datetime.now(timezone.utc)
            if (
                not force_refresh
                and self._token is not None
                and self._expires_at is not None
                and now + timedelta(seconds=30) < self._expires_at
            ):
                return self._token
            token = request_access_token(self._credentials)
            self._token = token
            self._expires_at = (
                now + timedelta(seconds=token.expires_in)
                if token.expires_in is not None
                else None
            )
            return token


def request_access_token(
    credentials: ApiCredentials,
    *,
    opener: Any = urllib.request.urlopen,
) -> AccessToken:
    """Request an OAuth client-credentials token over verified HTTPS."""
    client_id = credentials.client_id.strip()
    if not client_id or not credentials.client_secret:
        raise ValueError("Introduce el identificador y el secreto API.")

    basic_credentials = base64.b64encode(
        f"{client_id}:{credentials.client_secret}".encode("utf-8")
    ).decode("ascii")
    body = json.dumps({"grant_type": "client_credentials"}).encode("utf-8")
    request = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic_credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    started_at = time.perf_counter()
    logger.info(
        "1NCE HTTP request method=POST url=%s params=%s headers=%s",
        OAUTH_TOKEN_URL,
        {"grant_type": "client_credentials"},
        {"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with opener(
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
            "1NCE HTTP response method=POST url=%s status=%s elapsed_ms=%d "
            "headers=%s",
            OAUTH_TOKEN_URL,
            status_code,
            int((time.perf_counter() - started_at) * 1000),
            response_headers,
        )
    except urllib.error.HTTPError as exc:
        try:
            error_body = safe_error_body(
                exc.read(),
                credentials.client_id,
                credentials.client_secret,
                basic_credentials,
            )
        except (OSError, ValueError, AttributeError):
            error_body = "[no se pudo leer el cuerpo de error]"
        logger.warning(
            "1NCE HTTP response method=POST url=%s status=%s elapsed_ms=%d "
            "headers=%s error_body=%s",
            OAUTH_TOKEN_URL,
            exc.code,
            int((time.perf_counter() - started_at) * 1000),
            safe_response_headers(exc.headers),
            error_body,
        )
        raise OneNCEAuthError(_http_error_message(exc.code)) from None
    except (TimeoutError, urllib.error.URLError, OSError):
        logger.warning(
            "1NCE HTTP failure method=POST url=%s elapsed_ms=%d error=network",
            OAUTH_TOKEN_URL,
            int((time.perf_counter() - started_at) * 1000),
        )
        raise OneNCEAuthError(
            "No se pudo conectar con 1NCE. Comprueba la red e inténtalo de nuevo."
        ) from None

    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OneNCEAuthError(
            "1NCE devolvió una respuesta de autenticación no válida."
        ) from None
    if not isinstance(payload, dict):
        raise OneNCEAuthError(
            "1NCE devolvió una respuesta de autenticación no válida."
        )

    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise OneNCEAuthError(
            "La respuesta de 1NCE no contiene un token de acceso válido."
        )
    token = token.strip()
    expires_in = payload.get("expires_in")
    if expires_in is not None:
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            raise OneNCEAuthError(
                "La respuesta de 1NCE contiene una caducidad de token no válida."
            ) from None
        if expires_in <= 0:
            raise OneNCEAuthError(
                "La respuesta de 1NCE contiene una caducidad de token no válida."
            )
    return AccessToken(token, "Bearer", expires_in)


def safe_response_headers(headers: Any) -> dict[str, str]:
    """Return only response headers approved for diagnostic logging."""
    if headers is None:
        return {}
    return {
        str(name).lower(): str(value)
        for name, value in headers.items()
        if str(name).lower() in SAFE_RESPONSE_HEADER_NAMES
    }


def safe_error_body(body: bytes, *sensitive_values: str) -> str:
    """Decode and redact an HTTP error body before it can enter application logs."""
    text = body.decode("utf-8", errors="replace")
    for value in sorted((item for item in sensitive_values if item), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = re.sub(
        r"""(?i)(["']?(?:authorization|access_token|refresh_token|client_secret|password)["']?\s*[:=]\s*["']?)[^"',;}]+""",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", text)
    return text[:4000]


def load_api_credentials() -> ApiCredentials | None:
    keyring = _get_keyring()
    try:
        stored = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_CREDENTIALS_KEY)
    except keyring.errors.KeyringError:
        logger.error("No se pudieron leer las credenciales 1NCE del almacén seguro")
        raise CredentialStoreError(
            "No se pudieron leer las credenciales del almacén seguro de Windows."
        ) from None
    if stored is None:
        return None
    try:
        value = json.loads(stored)
        client_id = value["client_id"]
        client_secret = value["client_secret"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise CredentialStoreError(
            "Las credenciales guardadas no tienen un formato válido."
        ) from None
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        raise CredentialStoreError(
            "Las credenciales guardadas no tienen un formato válido."
        )
    return ApiCredentials(client_id, client_secret)


def store_api_credentials(credentials: ApiCredentials) -> None:
    if not credentials.client_id.strip() or not credentials.client_secret:
        raise ValueError("Introduce el identificador y el secreto API.")
    keyring = _get_keyring()
    stored = json.dumps(
        {
            "client_id": credentials.client_id.strip(),
            "client_secret": credentials.client_secret,
        }
    )
    try:
        keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_CREDENTIALS_KEY, stored)
    except keyring.errors.KeyringError:
        logger.error("No se pudieron guardar las credenciales 1NCE en el almacén seguro")
        raise CredentialStoreError(
            "No se pudieron guardar las credenciales en el almacén seguro de Windows."
        ) from None


def delete_api_credentials() -> None:
    keyring = _get_keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, KEYRING_CREDENTIALS_KEY)
    except keyring.errors.PasswordDeleteError:
        return
    except keyring.errors.KeyringError:
        logger.error("No se pudieron eliminar las credenciales 1NCE del almacén seguro")
        raise CredentialStoreError(
            "No se pudieron eliminar las credenciales del almacén seguro de Windows."
        ) from None


def _get_keyring() -> Any:
    try:
        import keyring
    except ImportError:
        raise CredentialStoreError(
            "Falta el paquete keyring. Reinstala las dependencias del proyecto."
        ) from None
    return keyring


def _http_error_message(status_code: int) -> str:
    if status_code in (400, 401):
        return (
            "1NCE rechazó las credenciales o el flujo OAuth. Verifica las credenciales "
            "API y el tipo de cuenta."
        )
    if status_code == 403:
        return "Las credenciales no tienen permiso para utilizar la API de 1NCE."
    if status_code == 404:
        return "No se encontró el endpoint de autenticación documentado de 1NCE."
    if status_code == 429:
        return "1NCE ha limitado temporalmente las solicitudes. Inténtalo más tarde."
    if status_code >= 500:
        return "El servicio de autenticación de 1NCE no está disponible temporalmente."
    return f"1NCE rechazó la solicitud de autenticación (HTTP {status_code})."


def load_device_iccid() -> str | None:
    """Load the stored device ICCID from secure Windows Credential Manager.
    
    Returns:
        The device ICCID string if stored, or None if not found.
        
    Raises:
        CredentialStoreError: If the credential store is unavailable.
    """
    keyring = _get_keyring()
    try:
        iccid = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_ICCID_KEY)
    except keyring.errors.KeyringError:
        logger.error("No se pudo leer el ICCID del almacén seguro")
        raise CredentialStoreError(
            "No se pudo leer el ICCID del almacén seguro de Windows."
        ) from None
    return iccid


def store_device_iccid(iccid: str) -> None:
    """Store the device ICCID in secure Windows Credential Manager.
    
    Args:
        iccid: The device ICCID to store.
        
    Raises:
        ValueError: If the ICCID is empty.
        CredentialStoreError: If the credential store is unavailable.
    """
    if not iccid or not iccid.strip():
        raise ValueError("El ICCID no puede estar vacío.")
    
    keyring = _get_keyring()
    try:
        keyring.set_password(
            KEYRING_SERVICE_NAME,
            KEYRING_ICCID_KEY,
            iccid.strip()
        )
    except keyring.errors.KeyringError:
        logger.error("No se pudo guardar el ICCID en el almacén seguro")
        raise CredentialStoreError(
            "No se pudo guardar el ICCID en el almacén seguro de Windows."
        ) from None


def delete_device_iccid() -> None:
    """Delete the stored device ICCID from Windows Credential Manager.
    
    Raises:
        CredentialStoreError: If the credential store is unavailable.
    """
    keyring = _get_keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, KEYRING_ICCID_KEY)
    except keyring.errors.PasswordDeleteError:
        return
    except keyring.errors.KeyringError:
        logger.error("No se pudo eliminar el ICCID del almacén seguro")
        raise CredentialStoreError(
            "No se pudo eliminar el ICCID del almacén seguro de Windows."
        ) from None
