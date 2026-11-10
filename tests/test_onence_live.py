"""Opt-in live 1NCE Historian integration test; never uses the serial port."""

from __future__ import annotations

import logging
import os

import pytest

from tdserial_iot.onence_api import (
    CredentialStoreError,
    OneNCEAuthenticator,
    load_api_credentials,
)
from tdserial_iot.onence_historian import OneNCEHistorianClient, render_payload

DEVICE_ID_ENV = "TDSERIAL_ONENCE_DEVICE_ID"


@pytest.mark.skipif(
    not os.environ.get(DEVICE_ID_ENV),
    reason=f"Define {DEVICE_ID_ENV} con el Device ID exacto de Device Inspector.",
)
def test_live_historian_using_stored_credentials():
    logging.basicConfig(level=logging.INFO)
    device_id = os.environ[DEVICE_ID_ENV].strip()
    if not device_id:
        pytest.fail(f"{DEVICE_ID_ENV} no puede estar vacío.")

    try:
        credentials = load_api_credentials()
    except CredentialStoreError as exc:
        pytest.fail(f"No se pudieron leer las credenciales guardadas: {exc}")
    if credentials is None:
        pytest.fail("No hay credenciales 1NCE guardadas en el almacén seguro.")

    authenticator = OneNCEAuthenticator(credentials)
    authenticator.get_access_token(force_refresh=True)
    page = OneNCEHistorianClient(authenticator).get_history_page(
        device_id,
        page_size=None,
        protocol=None,
    )

    print(f"Mensajes recibidos: {len(page.messages)}")
    for index, message in enumerate(page.messages, start=1):
        try:
            payload = render_payload(message, "JSON")
        except ValueError:
            payload = render_payload(message, "Texto")
        print(
            f"{index}. {message.timestamp_original} · {message.protocol} · "
            f"{message.content_type}\n{payload}"
        )
