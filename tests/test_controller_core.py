import json
import io
import urllib.error

import pytest

from tdserial_iot.controller_core import ControllerClient, ControllerError, ControllerStore, build_payload


def test_firmware_payload_and_limits():
    token = "example-test-token"
    assert json.loads(build_payload("buzzer", 123, token, 500)) == {
        "cmd": "buzzer", "id": 123, "duration_ms": 500, "token": token
    }
    assert json.loads(build_payload("reboot", 124, token))["confirm"] == "REBOOT"
    for args in [
        ("bad", 1, token, 500), ("buzzer", 1, token, 49),
        ("led_on", 0, token, 500), ("led_on", 1, "short", 500),
        ("led_on", 1, "x" * 400, 500),
    ]:
        with pytest.raises(ValueError):
            build_payload(*args)


def test_store_retains_counter_after_removing_device(tmp_path):
    store = ControllerStore(tmp_path / "controller.sqlite3")
    iccid = "1234567890123456789"
    store.save_device(iccid, "Example", "model", 3000, "")
    assert store.next_id(iccid) == 1
    store.delete_device(iccid)
    assert store.devices() == []
    store.save_device(iccid, "Example", "model", 3000, "")
    assert store.next_id(iccid) == 2
    record = store.record("2026-01-01", iccid, "Example", "led_on", 2, "SEND_NOW")
    store.update_record(record, "HTTP 202", "action")
    restored = ControllerStore(store.path)
    assert restored.history()[0][6:] == ("action", "SEND_NOW", "HTTP 202", "Sin confirmar")


def test_send_serializes_nested_payload_and_requires_202(monkeypatch):
    client = ControllerClient("https://example.test/management-api", "client", "secret")
    requests = []

    def action(method, path, body=None):
        requests.append((method, path, body))
        return 202, {"id": "request-1"}

    monkeypatch.setattr(client, "action", action)
    payload = build_payload("led_on", 5, "example-test-token")
    assert client.send("1234567890123456789", 3000, "SEND_NOW", payload) == "request-1"
    assert requests[0][2]["payload"] == payload
    assert json.loads(requests[0][2]["payload"])["id"] == 5
    monkeypatch.setattr(client, "action", lambda *_args: (200, {"id": "request-1"}))
    with pytest.raises(ControllerError):
        client.send("1234567890123456789", 3000, "SEND_NOW", payload)


def test_post_only_retries_after_401(monkeypatch):
    client = ControllerClient("https://example.test", "client", "secret")
    refreshes = []
    attempts = []
    monkeypatch.setattr(client, "authenticate", lambda force=False: refreshes.append(force))

    def request(*_args):
        attempts.append(1)
        if len(attempts) == 1:
            raise ControllerError("unauthorized", status_code=401)
        return 202, {"id": "abc"}

    monkeypatch.setattr(client, "_request", request)
    assert client.action("POST", "/x", {})[0] == 202
    assert refreshes == [False, True]
    assert len(attempts) == 2
    attempts.clear()
    monkeypatch.setattr(client, "_request", lambda *_args: (_ for _ in ()).throw(ControllerError("timeout")))
    with pytest.raises(ControllerError, match="timeout"):
        client.action("POST", "/x", {})
    assert refreshes == [False, True, False]


def test_oauth_and_status_use_expected_requests(monkeypatch):
    client = ControllerClient("https://example.test/management-api", "client", "secret")
    requests = []

    def request(method, path, body, auth):
        requests.append((method, path, body, auth))
        if path == "/oauth/token":
            return 200, {"access_token": "temporary", "expires_in": "3600"}
        return 200, {"status": "SUCCEEDED"}

    monkeypatch.setattr(client, "_request", request)
    assert client.status("action-123")["status"] == "SUCCEEDED"
    assert requests[0][:3] == ("POST", "/oauth/token", {"grant_type": "client_credentials"})
    assert requests[0][3].startswith("Basic ")
    assert requests[1] == (
        "GET", "/v1/integrate/devices/actions/requests/action-123", None,
        "Bearer temporary",
    )
    client.status("action-123")
    assert len(requests) == 3


def test_invalid_base_url_and_request_id():
    with pytest.raises(ValueError):
        ControllerClient("http://example.test", "client", "secret")
    client = ControllerClient("https://example.test", "client", "secret")
    with pytest.raises(ValueError):
        client.status("../invalid")


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 429, 500, 502, 503, 504])
def test_http_errors_do_not_expose_server_body(code, monkeypatch):
    client = ControllerClient("https://example.test", "client", "secret")

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, code, "failure", {}, io.BytesIO(b"private-secret")
            )

    monkeypatch.setattr(client, "_opener", Opener())
    with pytest.raises(ControllerError) as error:
        client._request("GET", "/x", None, "Bearer private-secret")
    assert error.value.status_code == code
    assert f"HTTP {code}" in str(error.value)
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/oauth/token", "endpoint de autenticación"),
        ("POST", "/v1/integrate/devices/12345678901234567890/actions/UDP", "Device Inspector"),
        ("GET", "/v1/integrate/devices/actions/requests/request1", "ID de acción"),
    ],
)
def test_http_404_diagnostics_identify_request_stage(method, path, expected, monkeypatch):
    client = ControllerClient("https://example.test/management-api", "client", "secret")

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 404, "failure", {}, io.BytesIO(b"private-secret")
            )

    monkeypatch.setattr(client, "_opener", Opener())
    with pytest.raises(ControllerError) as error:
        client._request(method, path, None, "Bearer private-secret")
    assert error.value.status_code == 404
    assert expected in str(error.value)
    assert "private-secret" not in str(error.value)
