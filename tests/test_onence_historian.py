import base64
import io
import json
import urllib.error
import urllib.parse
from datetime import datetime, timezone

import pytest

import tdserial_iot.onence_historian as historian
from tdserial_iot.iot_operations import TransmissionRecord
from tdserial_iot.main_window import OneNCEDevicesWorker, OneNCEHistoryWorker
from tdserial_iot.onence_historian import (
    DEVICE_LIST_PAGE_SIZE,
    HISTORIAN_MAX_PAGE_SIZE,
    OneNCEHistorianClient,
    OneNCEHistoryError,
    correlate_transmissions,
    parse_history_response,
    render_payload,
)
from tdserial_iot.onence_api import AccessToken


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class FakeAuthenticator:
    def get_access_token(self, *, force_refresh=False):
        return AccessToken("private-token", "Bearer", 3600)


def test_historian_parses_json_message_and_keeps_original_payload():
    original = '{"temp":21.3,"label":"café"}'
    page = parse_history_response(
        {
            "items": [
                {
                    "deviceId": "0012345",
                    "iccid": "0012345",
                    "time": "2026-11-09T19:39:00.000Z",
                    "protocol": "UDP",
                    "payload": original,
                }
            ]
        }
    )

    message = page.messages[0]
    assert message.payload_original == original
    assert message.payload_json == {"temp": 21.3, "label": "café"}
    assert message.content_type == "JSON"
    assert message.timestamp == datetime(
        2026, 11, 9, 19, 39, tzinfo=timezone.utc
    )
    assert render_payload(message, "JSON") == json.dumps(
        {"temp": 21.3, "label": "café"}, ensure_ascii=False, indent=2
    )


@pytest.mark.parametrize(
    ("protocol", "payload"),
    [("UDP", "null"), ("COAP", "bnVsbA==")],
)
def test_json_null_is_preserved_as_valid_json(protocol, payload):
    message = parse_history_response(
        {
            "items": [
                {
                    "deviceId": "00123",
                    "iccid": "00123",
                    "time": "2026-11-09T19:39:00Z",
                    "protocol": protocol,
                    "payload": payload,
                }
            ]
        }
    ).messages[0]

    assert message.payload_json_valid
    assert message.payload_json is None
    assert render_payload(message, "JSON") == "null"


def test_timezone_missing_is_preserved_as_naive_not_assumed_utc():
    page = parse_history_response(
        {
            "items": [
                {"time": "2026-11-09T19:39:00.000", "payload": "plain text"}
            ]
        }
    )

    assert page.messages[0].timestamp == datetime(2026, 11, 9, 19, 39)
    assert not page.messages[0].has_timezone


def test_coap_base64_is_decoded_by_protocol_without_changing_original():
    original = base64.b64encode(b'{"seq":5}').decode("ascii")
    page = parse_history_response(
        {"items": [{"protocol": "COAP", "payload": original}]}
    )
    message = page.messages[0]

    assert message.payload_original == original
    assert message.payload_bytes == b'{"seq":5}'
    assert message.payload_json == {"seq": 5}
    assert message.content_type == "JSON (Base64)"
    assert render_payload(message, "Base64 decodificado") == '{"seq":5}'
    assert render_payload(message, "HEX") == "7B 22 73 65 71 22 3A 35 7D"


def test_udp_base64_like_payload_is_not_assumed_to_be_base64():
    original = base64.b64encode(b"test data").decode("ascii")
    message = parse_history_response(
        {"items": [{"protocol": "UDP", "payload": original}]}
    ).messages[0]

    assert message.payload_original == original
    assert message.content_type == "Texto"
    assert message.payload_bytes == original.encode("utf-8")


def test_json_view_explicitly_decodes_base64_json_payload():
    original = base64.b64encode(b'{"temp":24.5,"hum":62}').decode("ascii")
    message = parse_history_response(
        {"items": [{"protocol": "UDP", "payload": original}]}
    ).messages[0]

    assert not message.payload_json_valid
    assert render_payload(message, "Base64 decodificado") == '{"temp":24.5,"hum":62}'
    assert json.loads(render_payload(message, "JSON")) == {
        "temp": 24.5,
        "hum": 62,
    }


def test_empty_page_and_repeated_pagination_token_shape():
    page = parse_history_response({"items": [], "nextToken": "token-2"})
    assert page.messages == ()
    assert page.next_token == "token-2"


def test_invalid_historian_response_is_reported():
    with pytest.raises(OneNCEHistoryError, match="items"):
        parse_history_response({"nextToken": "token"})
    with pytest.raises(OneNCEHistoryError, match="nextToken"):
        parse_history_response({"items": [], "nextToken": []})


def test_historian_client_encodes_iccid_filters_and_pagination(monkeypatch):
    captured = {}

    def urlopen(request, **kwargs):
        captured["request"] = request
        captured["kwargs"] = kwargs
        return FakeResponse({"items": [], "nextToken": "next-token"})

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    client = OneNCEHistorianClient(FakeAuthenticator())
    page = client.get_history_page(
        "00123",
        page_size=25,
        protocol="UDP",
        start_datetime=datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 11, 9, 11, 0, tzinfo=timezone.utc),
        next_token="token with/characters",
    )

    request = captured["request"]
    query = dict(urllib.parse.parse_qsl(request.full_url.split("?", 1)[1]))
    assert request.method == "GET"
    assert request.full_url.startswith(
        "https://api.1nce.com/management-api/v1/inspect/"
        "devices/00123/history?"
    )
    assert request.get_header("Authorization") == "Bearer private-token"
    assert query["pageSize"] == "25"
    assert query["protocol"] == "UDP"
    assert query["nextToken"] == "token with/characters"
    assert query["startDateTime"].endswith("+00:00")
    assert request.headers["Authorization"] == (
        f"Bearer {FakeAuthenticator().get_access_token().value}"
    )
    assert request.headers["Accept"] == "application/json"
    assert "Content-type" not in request.headers
    assert page.next_token == "next-token"


def test_historian_client_can_match_unfiltered_device_inspector_request(monkeypatch):
    captured = {}

    def urlopen(request, **_kwargs):
        captured["request"] = request
        return FakeResponse({"items": []})

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    auth = FakeAuthenticator()
    page = OneNCEHistorianClient(auth).get_history_page(
        "0012345678901234567",
        page_size=None,
        protocol=None,
    )
    request = captured["request"]

    assert request.method == "GET"
    assert request.full_url == (
        "https://api.1nce.com/management-api/v1/inspect/devices/"
        "0012345678901234567/history"
    )
    assert request.headers["Authorization"] == (
        f"Bearer {auth.get_access_token().value}"
    )
    assert request.headers["Accept"] == "application/json"
    assert "Content-type" not in request.headers
    assert page.messages == ()


def test_historian_page_size_and_naive_query_dates_are_rejected():
    client = OneNCEHistorianClient(FakeAuthenticator())
    with pytest.raises(ValueError, match=str(HISTORIAN_MAX_PAGE_SIZE)):
        client.get_history_page("00123", page_size=HISTORIAN_MAX_PAGE_SIZE + 1)
    with pytest.raises(ValueError, match="zona horaria"):
        client.get_history_page(
            "00123", start_datetime=datetime(2026, 11, 9, 10, 0)
        )


def test_historian_timeout_is_reported_without_retries(monkeypatch):
    calls = []

    def urlopen(*_args, **_kwargs):
        calls.append(True)
        raise TimeoutError

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    with pytest.raises(OneNCEHistoryError, match="conectar con Historian"):
        OneNCEHistorianClient(FakeAuthenticator()).get_history_page("00123")
    assert len(calls) == 1


def test_http_429_respects_retry_after_without_retrying(monkeypatch):
    calls = []

    def urlopen(*_args, **_kwargs):
        calls.append(True)
        raise urllib.error.HTTPError(
            "https://api.1nce.com/history",
            429,
            "rate limit",
            {"Retry-After": "30"},
            None,
        )

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    client = OneNCEHistorianClient(FakeAuthenticator())
    with pytest.raises(OneNCEHistoryError, match="30") as error:
        client.get_history_page("00123")

    assert error.value.retry_after == "30"
    assert len(calls) == 1


def test_historian_http_error_logs_safe_request_and_provider_body(
    monkeypatch, caplog
):
    def urlopen(request, **_kwargs):
        assert request.full_url.startswith(
            "https://api.1nce.com/management-api/v1/inspect/"
            "devices/0012345678901234567/history?"
        )
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "forbidden",
            hdrs={
                "Content-Type": "application/json",
                "X-Request-Id": "request-123",
                "Authorization": "must-not-be-logged",
                "Set-Cookie": "must-not-be-logged",
            },
            fp=io.BytesIO(
                b'{"error":"device_id_not_found","description":"No device",'
                b'"access_token":"private-token"}'
            ),
        )

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    with pytest.raises(OneNCEHistoryError) as error:
        OneNCEHistorianClient(FakeAuthenticator()).get_history_page(
            "0012345678901234567", protocol=None
        )

    assert "HTTP 403" in str(error.value)
    assert "permiso" not in str(error.value).lower()
    assert "method=GET" in caplog.text
    assert "status=403" in caplog.text
    assert "device_id_not_found" in caplog.text
    assert "request-123" in caplog.text
    assert "private-token" not in caplog.text
    assert "must-not-be-logged" not in caplog.text
    assert "Authorization" not in caplog.text


def test_historian_refreshes_once_after_unauthorized_response(monkeypatch):
    class RefreshingAuthenticator:
        def __init__(self):
            self.force_refreshes = []

        def get_access_token(self, *, force_refresh=False):
            self.force_refreshes.append(force_refresh)
            return AccessToken(
                f"token-{len(self.force_refreshes)}", "Bearer", 3600
            )

    calls = []

    def urlopen(request, **_kwargs):
        calls.append(request.get_header("Authorization"))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 401, "expired", None, None
            )
        return FakeResponse({"items": []})

    authenticator = RefreshingAuthenticator()
    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    page = OneNCEHistorianClient(authenticator).get_history_page("00123")

    assert page.messages == ()
    assert calls == ["Bearer token-1", "Bearer token-2"]
    assert authenticator.force_refreshes == [False, True]


def test_transmission_correlation_prefers_seq_then_uses_payload_and_time():
    moment = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
    records = [
        TransmissionRecord(
            timestamp=moment,
            protocol="UDP",
            host="udp.os.1nce.com",
            port=4445,
            socket_id=0,
            payload_size=9,
            payload_type="JSON",
            result="sent",
            duration_ms=10,
            payload=b'{"seq":7}',
            device_id="00123",
            sequence_id="7",
        ),
        TransmissionRecord(
            timestamp=moment,
            protocol="UDP",
            host="udp.os.1nce.com",
            port=4445,
            socket_id=0,
            payload_size=4,
            payload_type="ASCII / UTF-8",
            result="sent",
            duration_ms=10,
            payload=b"test",
            device_id="00123",
        ),
    ]
    messages = [
        historian.parse_cloud_message(
            {
                "iccid": "00123",
                "deviceId": "00123",
                "time": "2026-11-09T20:20:00Z",
                "protocol": "UDP",
                "payload": '{"seq":7}',
            }
        ),
        historian.parse_cloud_message(
            {
                "iccid": "00123",
                "deviceId": "00123",
                "time": "2026-11-09T20:02:00Z",
                "protocol": "UDP",
                "payload": "test",
            }
        ),
    ]

    results = correlate_transmissions(
        records, messages, "00123", history_queried=True
    )
    assert [item.status for item in results] == [
        "Confirmado en 1NCE",
        "Confirmado en 1NCE",
    ]
    assert [item.cloud_index for item in results] == [0, 1]


def test_duplicate_payload_matches_are_ambiguous_and_missing_is_not_final_loss():
    moment = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
    record = TransmissionRecord(
        timestamp=moment,
        protocol="UDP",
        host="udp.os.1nce.com",
        port=4445,
        socket_id=0,
        payload_size=4,
        payload_type="ASCII / UTF-8",
        result="sent",
        duration_ms=10,
        payload=b"test",
        device_id="00123",
    )
    duplicate = historian.parse_cloud_message(
        {
            "iccid": "00123",
            "deviceId": "00123",
            "time": "2026-11-09T20:01:00Z",
            "protocol": "UDP",
            "payload": "test",
        }
    )
    results = correlate_transmissions(
        [record], [duplicate, duplicate], "00123", history_queried=True
    )
    assert results[0].status == "Correspondencia ambigua en 1NCE"
    pending = correlate_transmissions(
        [record], [], "00123", history_queried=False
    )
    assert pending[0].status == "Pendiente de confirmación en 1NCE"


def test_fetch_all_stops_at_configured_limit(monkeypatch):
    responses = iter(
        [
            {"items": [{"payload": "one"}], "nextToken": "token-1"},
            {"items": [{"payload": "two"}], "nextToken": "token-2"},
        ]
    )
    calls = []

    def urlopen(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse(next(responses))

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    worker = OneNCEHistoryWorker(
        FakeAuthenticator(),
        "00123",
        page_size=1,
        protocol="UDP",
        start_datetime=None,
        end_datetime=None,
        next_token=None,
        fetch_all=True,
        max_records=2,
    )
    results = []
    worker.completed.connect(lambda *args: results.append(args))
    worker.run()

    assert len(calls) == 2
    assert results[0][0] is True
    assert len(results[0][1].messages) == 2
    assert results[0][1].next_token == "token-2"


def test_fetch_all_stops_when_historian_repeats_a_token(monkeypatch):
    calls = []

    def urlopen(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse({"items": [], "nextToken": "same-token"})

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    worker = OneNCEHistoryWorker(
        FakeAuthenticator(),
        "00123",
        page_size=1,
        protocol="UDP",
        start_datetime=None,
        end_datetime=None,
        next_token=None,
        fetch_all=True,
        max_records=50,
    )
    results = []
    worker.completed.connect(lambda *args: results.append(args))
    worker.run()

    assert len(calls) == 2
    assert results[0][0] is False
    assert "repitió un nextToken" in results[0][2]


def test_all_devices_history_omits_iccid_and_keeps_filters_and_next_token(monkeypatch):
    captured = {}

    def urlopen(request, **_kwargs):
        captured["request"] = request
        return FakeResponse({"items": [], "nextToken": "next-2"})

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    page = OneNCEHistorianClient(FakeAuthenticator()).get_all_history_page(
        page_size=25,
        protocol="COAP",
        start_datetime=datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 11, 9, 11, 0, tzinfo=timezone.utc),
        next_token="next-1",
    )
    request = captured["request"]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.full_url).query))

    assert urllib.parse.urlsplit(request.full_url).path == (
        "/management-api/v1/inspect/devices/history"
    )
    assert request.method == "GET"
    assert query["pageSize"] == "25"
    assert query["protocol"] == "COAP"
    assert query["nextToken"] == "next-1"
    assert "startDateTime" in query
    assert "endDateTime" in query
    assert "iccid" not in query
    assert "deviceId" not in request.full_url
    assert page.next_token == "next-2"


def test_device_listing_and_sim_metadata_pagination_keep_duplicate_labels(monkeypatch):
    requests = []

    def urlopen(request, **_kwargs):
        requests.append(request.full_url)
        parsed = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path.endswith("/inspect/devices"):
            if query.get("nextToken") == "devices-2":
                return FakeResponse({"items": [{"deviceId": "iccid-3"}]})
            return FakeResponse(
                {
                    "items": [
                        {"deviceId": "iccid-1"},
                        {"deviceId": "iccid-2"},
                    ],
                    "nextToken": "devices-2",
                }
            )
        if parsed.path.endswith("/sims"):
            page_number = int(query["page"])
            if page_number == 1:
                sims = [
                    {"iccid": "iccid-1", "label": "Equipo"},
                    {"iccid": "iccid-2", "label": "Equipo"},
                ]
                sims.extend(
                    {"iccid": f"other-{index}", "label": f"Equipo {index}"}
                    for index in range(DEVICE_LIST_PAGE_SIZE - len(sims))
                )
                return FakeResponse(
                    {"items": sims, "total": DEVICE_LIST_PAGE_SIZE + 1}
                )
            return FakeResponse(
                {"items": [{"iccid": "iccid-3"}], "total": DEVICE_LIST_PAGE_SIZE + 1}
            )
        raise AssertionError(f"Unexpected endpoint: {request.full_url}")

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    client = OneNCEHistorianClient(FakeAuthenticator())
    iccids = client.get_device_iccids()
    labels = client.get_sim_labels()

    assert iccids == ("iccid-1", "iccid-2", "iccid-3")
    assert labels["iccid-1"] == labels["iccid-2"] == "Equipo"
    assert "iccid-3" not in labels
    assert any("nextToken=devices-2" in url for url in requests)
    assert any("page=2" in url for url in requests)


def test_inspect_device_pagination_rejects_repeated_next_token(monkeypatch):
    def urlopen(*_args, **_kwargs):
        return FakeResponse({"items": [], "nextToken": "repeat"})

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    with pytest.raises(OneNCEHistoryError, match="repitió un nextToken"):
        OneNCEHistorianClient(FakeAuthenticator()).get_device_iccids()


def test_inspect_device_pagination_uses_supported_page_size_and_page_number(
    monkeypatch,
):
    requests = []

    def urlopen(request, **_kwargs):
        requests.append(urllib.parse.urlsplit(request.full_url))
        page = dict(urllib.parse.parse_qsl(requests[-1].query)).get("page", "1")
        if page == "1":
            return FakeResponse(
                {
                    "items": [
                        {"deviceId": f"iccid-{index}"}
                        for index in range(DEVICE_LIST_PAGE_SIZE)
                    ]
                }
            )
        return FakeResponse({"items": [{"deviceId": "iccid-last"}]})

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    iccids = OneNCEHistorianClient(FakeAuthenticator()).get_device_iccids()

    first_query = dict(urllib.parse.parse_qsl(requests[0].query))
    second_query = dict(urllib.parse.parse_qsl(requests[1].query))
    assert first_query["pageSize"] == "50"
    assert second_query == {"pageSize": "50", "page": "2"}
    assert len(iccids) == DEVICE_LIST_PAGE_SIZE + 1


@pytest.mark.parametrize("status_code", [401, 403])
def test_all_device_history_reports_authentication_and_authorization_errors(
    monkeypatch, status_code
):
    calls = []

    def urlopen(request, **_kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, status_code, "denied", {}, io.BytesIO(b"{}")
        )

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    with pytest.raises(OneNCEHistoryError) as error:
        OneNCEHistorianClient(FakeAuthenticator()).get_all_history_page()
    assert error.value.status_code == status_code
    assert len(calls) == (2 if status_code == 401 else 1)


def test_sim_metadata_authorization_failure_does_not_block_iccid_selection(
    monkeypatch,
):
    def urlopen(request, **_kwargs):
        if urllib.parse.urlsplit(request.full_url).path.endswith("/inspect/devices"):
            return FakeResponse({"items": [{"deviceId": "iccid-1"}]})
        raise urllib.error.HTTPError(
            request.full_url, 403, "denied", {}, io.BytesIO(b"{}")
        )

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    client = OneNCEHistorianClient(FakeAuthenticator())
    assert client.get_device_iccids() == ("iccid-1",)
    with pytest.raises(OneNCEHistoryError) as error:
        client.get_sim_labels()
    assert error.value.status_code == 403


def test_device_worker_falls_back_to_iccid_when_labels_are_forbidden(monkeypatch):
    monkeypatch.setattr(
        OneNCEHistorianClient,
        "get_device_iccids",
        lambda self: ("iccid-1", "iccid-2"),
    )

    def labels_forbidden(self):
        raise OneNCEHistoryError("HTTP 403", status_code=403)

    monkeypatch.setattr(OneNCEHistorianClient, "get_sim_labels", labels_forbidden)
    worker = OneNCEDevicesWorker(FakeAuthenticator())
    results = []
    worker.completed.connect(lambda *args: results.append(args))
    worker.run()

    assert results[0][0] is True
    assert [device.iccid for device in results[0][1]] == ["iccid-1", "iccid-2"]
    assert all(not device.label for device in results[0][1])
    assert "se muestran los ICCID" in results[0][2]


def test_fetch_all_history_removes_duplicate_messages_from_adjacent_pages(
    monkeypatch,
):
    first = {
        "iccid": "iccid-1",
        "time": "2026-11-09T19:39:00Z",
        "protocol": "UDP",
        "payload": '{"seq":1}',
    }
    second = {
        "iccid": "iccid-1",
        "time": "2026-11-09T19:40:00Z",
        "protocol": "UDP",
        "payload": '{"seq":2}',
    }
    responses = iter(
        [
            {"items": [first], "nextToken": "token-2"},
            {"items": [first, second]},
        ]
    )
    calls = []

    def urlopen(request, **_kwargs):
        calls.append(request.full_url)
        return FakeResponse(next(responses))

    monkeypatch.setattr(historian.urllib.request, "urlopen", urlopen)
    worker = OneNCEHistoryWorker(
        FakeAuthenticator(),
        None,
        page_size=25,
        protocol="UDP",
        start_datetime=None,
        end_datetime=None,
        next_token=None,
        fetch_all=True,
        max_records=20,
    )
    results = []
    worker.completed.connect(lambda *args: results.append(args))
    worker.run()

    assert len(calls) == 2
    assert "nextToken=token-2" in calls[1]
    assert results[0][0] is True
    assert len(results[0][1].messages) == 2
