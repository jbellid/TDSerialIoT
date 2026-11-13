import json
import io
import sqlite3
import urllib.error

import pytest

from tdserial_iot.controller_core import ControllerClient, ControllerError, ControllerStore, build_payload
from tdserial_iot.onence_historian import parse_cloud_message


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
    assert restored.history()[0][6:] == ("action", "SEND_NOW", "HTTP 202", "Sin confirmar", "")


ICCID = "1234567890123456789"


def _ack(*, device=ICCID, command_id=7, command="led_on", status="executed",
         timestamp="2026-11-13T10:01:00Z", event="command_ack", **extra):
    return parse_cloud_message({
        "deviceId": device, "time": timestamp, "protocol": "UDP",
        "payload": json.dumps({"event": event, "cmd_id": command_id, "cmd": command, "status": status}),
        **extra,
    })


def _store_with_record(tmp_path, command="led_on"):
    store = ControllerStore(tmp_path / "controller.sqlite3")
    store.record("2026-11-13T10:00:00+00:00", ICCID, "Device", command, 7, "SEND_NOW")
    return store


def test_ack_correlation_persists_without_changing_api_status(tmp_path):
    store = _store_with_record(tmp_path)
    store.update_record(store.history()[0][0], "Error o resultado desconocido")
    assert store.correlate_acks([_ack()]) == 1
    row = ControllerStore(store.path).history()[0]
    assert row[8:] == ("Error o resultado desconocido", "Ejecutado según dispositivo", "2026-11-13T10:01:00Z")
    assert store.correlate_acks([_ack()]) == 0
    assert store.history()[0] == row


def test_ack_accepted_and_conflicting_evidence_remains_ambiguous(tmp_path):
    store = _store_with_record(tmp_path)
    store.correlate_acks([_ack(status="accepted")])
    assert store.history()[0][9] == "Aceptado por el dispositivo"
    store.correlate_acks([_ack(status="executed")])
    assert store.history()[0][9] == "Correlación ambigua"
    store.correlate_acks([_ack(status="accepted")])
    assert store.history()[0][9] == "Correlación ambigua"


@pytest.mark.parametrize(("timestamp", "expected"), [
    ("2026-11-13T10:58:00+01:00", "Ejecutado según dispositivo"),
    ("2026-11-13T10:57:59+01:00", "Sin confirmar"),
    ("2026-11-16T10:00:00Z", "Ejecutado según dispositivo"),
])
def test_ack_clock_tolerance_and_delayed_delivery(tmp_path, timestamp, expected):
    store = _store_with_record(tmp_path)
    store.correlate_acks([_ack(timestamp=timestamp)])
    assert store.history()[0][9] == expected


@pytest.mark.parametrize("message", [
    _ack(device="9876543210987654321"),
    _ack(iccid="9876543210987654321"),
    _ack(command_id=8),
    _ack(command_id=True),
    _ack(event="status"),
    _ack(status="failed"),
    _ack(timestamp="2026-11-13T09:00:00Z"),
    _ack(timestamp=""),
    parse_cloud_message({"deviceId": ICCID, "time": "2026-11-13T10:01:00Z", "payload": "bad"}),
])
def test_unusable_ack_does_not_confirm_command(tmp_path, message):
    store = _store_with_record(tmp_path)
    assert store.correlate_acks([message]) == 0
    assert store.history()[0][9:] == ("Sin confirmar", "")


def test_wrong_command_or_duplicate_local_ids_are_ambiguous(tmp_path):
    store = _store_with_record(tmp_path)
    store.correlate_acks([_ack(command="led_off")])
    assert store.history()[0][9] == "Correlación ambigua"
    other = ControllerStore(tmp_path / "other.sqlite3")
    for _ in range(2):
        other.record("2026-11-13T10:00:00+00:00", ICCID, "Device", "led_on", 7, "SEND_NOW")
    assert other.correlate_acks([_ack()]) == 2
    assert all(row[9] == "Correlación ambigua" for row in other.history())


def test_custom_command_does_not_claim_confirmation(tmp_path):
    store = _store_with_record(tmp_path, command="Personalizado")
    assert store.correlate_acks([_ack()]) == 0
    assert store.history()[0][9] == "Sin confirmar"


def test_udp_base64_ack_is_decoded(tmp_path):
    import base64
    store = _store_with_record(tmp_path)
    content = json.dumps({"event": "command_ack", "cmd_id": 7, "cmd": "led_on", "status": "executed"})
    message = parse_cloud_message({
        "iccid": ICCID, "time": "2026-11-13T10:01:00Z", "protocol": "UDP",
        "payload": base64.b64encode(content.encode()).decode(),
    })
    assert store.correlate_acks([message]) == 1
    assert store.history()[0][9] == "Ejecutado según dispositivo"


def test_real_historian_ack_without_timezone_is_correlated_with_explicit_uncertainty(tmp_path):
    store = ControllerStore(tmp_path / "controller.sqlite3")
    store.record("2026-11-13T11:47:03+01:00", ICCID, "Device", "led_toggle", 75, "SEND_NOW")
    message = parse_cloud_message({
        "time": "2026-11-13T11:47:19.300", "iccid": ICCID, "deviceId": ICCID,
        "protocol": "UDP",
        "payload": "eyJpZCI6InRkc2ltNzA4MC0wMSIsImV2ZW50IjoiY29tbWFuZF9hY2siLCJjbWRfaWQiOjc1LCJzdGF0dXMiOiJleGVjdXRlZCIsImNtZCI6ImxlZF90b2dnbGUiLCJsZWQiOnRydWV9",
    })
    assert store.correlate_acks([message]) == 1
    assert store.history()[0][9:] == (
        "Ejecutado según dispositivo (fecha sin zona)", "2026-11-13T11:47:19.300"
    )
    assert store.correlate_acks([message]) == 0


def test_naive_ack_never_assumes_offset_and_rejects_provably_old_time(tmp_path):
    store = _store_with_record(tmp_path)
    assert store.correlate_acks([_ack(timestamp="2026-11-12T21:57:59")]) == 0
    assert store.correlate_acks([_ack(timestamp="2026-11-12T21:58:00")]) == 1
    assert store.history()[0][9] == "Ejecutado según dispositivo (fecha sin zona)"


def test_timezone_specific_ack_upgrades_equivalent_naive_evidence(tmp_path):
    store = _store_with_record(tmp_path)
    store.correlate_acks([_ack(timestamp="2026-11-13T10:01:00")])
    assert store.correlate_acks([_ack(timestamp="2026-11-13T10:01:00Z")]) == 1
    assert store.history()[0][9:] == ("Ejecutado según dispositivo", "2026-11-13T10:01:00Z")
    assert store.correlate_acks([_ack(timestamp="2026-11-13T10:01:00")]) == 0
    store.correlate_acks([_ack(timestamp="2026-11-13T10:01:00", status="accepted")])
    assert store.history()[0][9] == "Correlación ambigua"


def test_existing_database_is_migrated_without_losing_history(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE history (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL,
                iccid TEXT NOT NULL, name TEXT NOT NULL, command TEXT NOT NULL,
                command_id INTEGER NOT NULL, action_id TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL, api_status TEXT NOT NULL,
                execution_status TEXT NOT NULL DEFAULT 'Sin confirmar'
            );
            INSERT INTO history(created,iccid,name,command,command_id,mode,api_status)
            VALUES('2026-11-13T10:00:00+00:00','1234567890123456789','Device','led_on',7,'SEND_NOW','HTTP 202');
        """)
    store = ControllerStore(path)
    assert store.history()[0][9:] == ("Sin confirmar", "")
    assert store.correlate_acks([_ack()]) == 1
    assert ControllerStore(path).history()[0][9] == "Ejecutado según dispositivo"


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
