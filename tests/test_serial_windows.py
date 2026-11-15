import sys
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows serial backend")

if sys.platform == "win32":
    from tdserial_iot import serial_windows


@pytest.fixture
def windows_api(monkeypatch):
    api = serial_windows.win32
    create_file = Mock(return_value=100)
    create_event = Mock(side_effect=[101, 102])
    close_handle = Mock(return_value=True)
    purge = Mock()
    monkeypatch.setattr(api, "CreateFile", create_file)
    monkeypatch.setattr(api, "CreateEvent", create_event)
    monkeypatch.setattr(api, "CloseHandle", close_handle)
    monkeypatch.setattr(api, "PurgeComm", purge)
    monkeypatch.setattr(api, "GetCommTimeouts", Mock(return_value=True))
    monkeypatch.setattr(api, "SetCommTimeouts", Mock(return_value=True))
    monkeypatch.setattr(serial_windows.SerialWithoutInputPurge, "_reconfigure_port", Mock())
    return create_file, create_event, close_handle, purge


def test_windows_open_does_not_purge_and_close_releases_handles(windows_api, monkeypatch):
    create_file, _, close_handle, purge = windows_api
    port = serial_windows.SerialWithoutInputPurge(port=None, timeout=0)
    port.port = "COM6"
    monkeypatch.setattr(port, "cancel_read", Mock())
    monkeypatch.setattr(port, "cancel_write", Mock())
    port.open()
    assert port.is_open
    assert create_file.call_args.args[0] == "\\\\.\\COM6"
    purge.assert_not_called()
    port.close()
    assert not port.is_open
    assert [call.args[0] for call in close_handle.call_args_list] == [101, 102, 100]


def test_windows_open_cleans_up_if_event_creation_fails(windows_api):
    _, create_event, close_handle, purge = windows_api
    create_event.side_effect = [101, 0]
    port = serial_windows.SerialWithoutInputPurge(port=None, timeout=0)
    port.port = "COM6"
    with pytest.raises(serial_windows.serial.SerialException, match="write event"):
        port.open()
    assert not port.is_open
    assert port._port_handle is None
    purge.assert_not_called()
    assert [call.args[0] for call in close_handle.call_args_list] == [101, 100]
