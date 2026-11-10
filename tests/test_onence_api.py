import base64
import io
import json
import urllib.error

import pytest

import tdserial_iot.onence_api as onence_api
from tdserial_iot.onence_api import (
    AccessToken,
    ApiCredentials,
    OneNCEAuthError,
    OneNCEAuthenticator,
    request_access_token,
)


class FakeResponse:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self._value).encode("utf-8")


def test_oauth_request_uses_documented_basic_auth_and_json_grant():
    captured = {}

    def opener(request, **kwargs):
        captured["request"] = request
        captured["kwargs"] = kwargs
        return FakeResponse(
            {"access_token": "private-token", "token_type": "Bearer", "expires_in": 3600}
        )

    token = request_access_token(
        ApiCredentials("api-client", "private-secret"), opener=opener
    )

    request = captured["request"]
    assert request.full_url == onence_api.OAUTH_TOKEN_URL
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Basic " + base64.b64encode(
        b"api-client:private-secret"
    ).decode("ascii")
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"grant_type": "client_credentials"}
    assert captured["kwargs"]["timeout"] == onence_api.HTTP_TIMEOUT_SECONDS
    assert token == AccessToken("private-token", "Bearer", 3600)


def test_oauth_uses_bearer_scheme_even_if_provider_returns_another_type():
    def opener(*_args, **_kwargs):
        return FakeResponse(
            {
                "access_token": "access-only",
                "token_type": "MAC",
                "expires_in": 3600,
                "id_token": "must-not-be-used",
            }
        )

    token = request_access_token(
        ApiCredentials("api-client", "private-secret"), opener=opener
    )

    assert token == AccessToken("access-only", "Bearer", 3600)


def test_oauth_error_diagnostics_redact_credentials_and_authorization(caplog):
    error_body = (
        b'{"error":"invalid_client","client_secret":"private-secret",'
        b'"Authorization":"Basic cHJpdmF0ZTpwYXNz"}'
    )

    def opener(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            onence_api.OAUTH_TOKEN_URL,
            403,
            "failure",
            hdrs={"Content-Type": "application/json", "Set-Cookie": "private=value"},
            fp=io.BytesIO(error_body),
        )

    with pytest.raises(OneNCEAuthError):
        request_access_token(
            ApiCredentials("api-client", "private-secret"), opener=opener
        )

    assert "status=403" in caplog.text
    assert '"error":"invalid_client"' in caplog.text
    assert "private-secret" not in caplog.text
    assert "cHJpdmF0ZTpwYXNz" not in caplog.text
    assert "private=value" not in caplog.text


@pytest.mark.parametrize(
    ("status_code", "message_fragment"),
    [
        (401, "credenciales"),
        (403, "permiso"),
        (429, "limitado"),
        (503, "no está disponible"),
    ],
)
def test_http_auth_errors_are_sanitized(status_code, message_fragment):
    def opener(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            onence_api.OAUTH_TOKEN_URL,
            status_code,
            "failure",
            hdrs=None,
            fp=None,
        )

    with pytest.raises(OneNCEAuthError) as error:
        request_access_token(
            ApiCredentials("api-client", "secret-that-must-not-leak"),
            opener=opener,
        )

    assert message_fragment in str(error.value)
    assert "secret-that-must-not-leak" not in str(error.value)


def test_invalid_token_response_is_rejected_without_exposing_response():
    def opener(*_args, **_kwargs):
        return FakeResponse({"error": "secret-like-provider-detail"})

    with pytest.raises(OneNCEAuthError) as error:
        request_access_token(ApiCredentials("client", "secret"), opener=opener)

    assert "secret-like-provider-detail" not in str(error.value)


def test_authenticator_reuses_unexpired_token_and_refreshes_when_forced(monkeypatch):
    tokens = iter(
        [
            AccessToken("token-1", "Bearer", 3600),
            AccessToken("token-2", "Bearer", 3600),
        ]
    )
    requests = []

    def request(_credentials):
        requests.append(True)
        return next(tokens)

    monkeypatch.setattr(onence_api, "request_access_token", request)
    authenticator = OneNCEAuthenticator(ApiCredentials("client", "secret"))

    first = authenticator.get_access_token()
    cached = authenticator.get_access_token()
    refreshed = authenticator.get_access_token(force_refresh=True)

    assert first.value == cached.value == "token-1"
    assert refreshed.value == "token-2"
    assert len(requests) == 2


def test_credentials_are_stored_as_a_single_secure_keyring_entry(monkeypatch):
    class FakeKeyring:
        values = {}

        def set_password(self, service, username, password):
            self.values[(service, username)] = password

        def get_password(self, service, username):
            return self.values.get((service, username))

    fake_keyring = FakeKeyring()
    monkeypatch.setattr(onence_api, "_get_keyring", lambda: fake_keyring)
    credentials = ApiCredentials("api-client", "api-secret")

    onence_api.store_api_credentials(credentials)

    saved = next(iter(fake_keyring.values.values()))
    assert json.loads(saved) == {
        "client_id": "api-client",
        "client_secret": "api-secret",
    }
    assert onence_api.load_api_credentials() == credentials
