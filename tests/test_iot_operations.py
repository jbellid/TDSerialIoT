import pytest

from tdserial_iot.iot_operations import (
    UDP_MAX_PAYLOAD_BYTES,
    build_cn_activate_command,
    build_cn_config_command,
    build_open_socket_command,
    build_send_command,
    encode_payload,
    parse_application_config,
    parse_application_context,
    parse_socket_state,
)


def test_payload_formats_preserve_exact_bytes_and_json_whitespace():
    assert encode_payload("ASCII / UTF-8", "café") == "café".encode("utf-8")
    json_text = '{ "temp": 24.5, "ok": true }\n'
    assert encode_payload("JSON", json_text) == json_text.encode("utf-8")
    assert encode_payload("HEX", "00 0A 0D 1A FF") == b"\x00\n\r\x1a\xff"
    assert encode_payload("Binario de archivo", file_data=b"\x00\xff") == b"\x00\xff"


@pytest.mark.parametrize(
    ("payload_format", "text"),
    [
        ("HEX", "0"),
        ("HEX", "0G"),
        ("JSON", '{"x":'),
        ("JSON", '{"x": NaN}'),
        ("Binario de archivo", ""),
    ],
)
def test_invalid_payloads_are_reported(payload_format, text):
    with pytest.raises(ValueError):
        encode_payload(payload_format, text)


def test_context_commands_are_exact_and_reject_at_command_injection():
    assert build_cn_config_command(0, "iot.1nce.net") == (
        b'AT+CNCFG=0,1,"iot.1nce.net"\r'
    )
    assert build_cn_activate_command(0) == b"AT+CNACT=0,1\r"
    with pytest.raises(ValueError):
        build_cn_config_command(0, 'internet","");AT')


def test_open_socket_command_uses_sim7080_udp_and_documented_manual_receive_mode():
    command = build_open_socket_command(0, 0, "udp.os.1nce.com", 4445, 25_000)
    assert command.data == (
        b'AT+CAOPEN=0,0,"UDP","udp.os.1nce.com",4445,0\r'
    )
    assert command.confirmation_prefix == b"+CAOPEN: 0,"


def test_casend_uses_exact_utf8_length_prompt_and_unterminated_payload():
    payload = '{"city":"Málaga"}'.encode("utf-8")
    command = build_send_command(0, payload, 10_000, 5000, 30_000)
    assert command.data == f"AT+CASEND=0,{len(payload)},5000\r".encode()
    assert command.interaction is not None
    assert command.interaction.prompt == b">"
    assert command.interaction.payload == payload
    assert command.interaction.response_timeout_ms == 30_000
    assert not command.interaction.payload.endswith((b"\r", b"\n"))
    with pytest.raises(ValueError, match=str(UDP_MAX_PAYLOAD_BYTES)):
        build_send_command(0, b"x" * (UDP_MAX_PAYLOAD_BYTES + 1), 1000, 5000, 1000)


def test_sim7080_context_and_socket_status_parsers():
    context = parse_application_context(
        [
            b'+CNACT: 0,1,"10.0.0.1"\r\n',
            b'+CNACT: 1,0,"0.0.0.0"\r\n',
        ],
        0,
    )
    config = parse_application_config(
        [b'+CNCFG: 0,1,"iot.1nce.net","","",0\r\n'], 0
    )
    assert context is not None and context.active and context.address == "10.0.0.1"
    assert config is not None and config.apn == "iot.1nce.net"
    assert parse_socket_state([b"+CASTATE: 0,1\r\n"], 0) == 1
    assert parse_socket_state([b"+CASTATE: 1,1\r\n"], 0) == 0
