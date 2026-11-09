import os
from pathlib import Path

import pytest

from tdserial_iot.main import configure_qt_plugin_paths
from tdserial_iot.main_window import MainWindow
from tdserial_iot.protocol import encode_at_command, encode_at_commands


@pytest.mark.parametrize(
    ("terminator", "expected"),
    [
        ("Ninguna", b"AT"),
        ("CR", b"AT\r"),
        ("LF", b"AT\n"),
        ("CRLF", b"AT\r\n"),
    ],
)
def test_at_terminators(terminator, expected):
    assert encode_at_command("AT", terminator) == expected


def test_at_command_uses_ascii_and_reports_encoded_byte_length():
    command = "AT+CSQ"
    payload = encode_at_command(command, "CR")
    assert len(payload) == len(command.encode("ascii")) + 1
    assert payload == b"AT+CSQ\r"


def test_non_ascii_command_is_rejected():
    with pytest.raises(UnicodeEncodeError):
        encode_at_command("AT+Ñ")


def test_unknown_terminator_is_rejected():
    with pytest.raises(ValueError):
        encode_at_command("AT", "CRCR")


def test_multiline_commands_encode_each_nonempty_line_separately():
    assert encode_at_commands("\nATI\nAT+CSQ\r\n\nAT+CPIN?\n", "CR") == [
        b"ATI\r",
        b"AT+CSQ\r",
        b"AT+CPIN?\r",
    ]


def test_multiline_commands_reject_non_ascii_before_returning_any_bytes():
    with pytest.raises(UnicodeEncodeError):
        encode_at_commands("ATI\nAT+Ñ\n", "CR")


def test_qt_plugins_are_configured_from_pyside_installation(monkeypatch):
    monkeypatch.setenv("QT_PLUGIN_PATH", "incorrect-qt-path")
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", "incorrect-platform-path")
    configure_qt_plugin_paths()
    from PySide6 import __file__ as pyside_file

    plugin_dir = Path(pyside_file).resolve().parent / "plugins"
    assert os.environ["QT_PLUGIN_PATH"] == str(plugin_dir)
    assert os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(plugin_dir / "platforms")


@pytest.mark.parametrize(
    ("data", "expected_special", "expected_plain"),
    [
        (b"AT\r\nOK\r\n", "AT␍␊OK␍␊", "ATOK"),
        (b"\x00\t\x1b\x7f", "␀␉␛␡", ""),
        (b"\x80\xff", r"\x80\xFF", ""),
        (b"\\", "\\", "\\"),
    ],
)
def test_ascii_monitor_toggles_special_characters(data, expected_special, expected_plain):
    assert MainWindow._visible_ascii(data, show_special_characters=True) == expected_special
    assert MainWindow._visible_ascii(data, show_special_characters=False) == expected_plain
