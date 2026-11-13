import csv
import json
import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QTableWidget

import tdserial_iot.controller_panel as panel_module
from tdserial_iot.controller_core import ControllerStore
from tdserial_iot.main import configure_qt_plugin_paths
from tdserial_iot.onence_api import ApiCredentials
from tdserial_iot.onence_historian import parse_cloud_message


def test_remote_command_preview_send_and_persistence(tmp_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    configure_qt_plugin_paths()
    app = QApplication.instance() or QApplication([])
    store = ControllerStore(tmp_path / "controller.sqlite3")
    iccid = "1234567890123456789"
    store.save_device(iccid, "Example", "LilyGO", 3000, "")
    monkeypatch.setattr(panel_module, "ControllerStore", lambda: store)
    monkeypatch.setattr(panel_module.keyring, "get_password", lambda *_args: "test-token-123456")
    sent = []

    class FakeClient:
        def send(self, device_id, port, mode, payload):
            sent.append((device_id, port, mode, json.loads(payload)))
            return "request123"

    panel = panel_module.ControllerPanel(lambda: ApiCredentials("client", "secret"))
    monkeypatch.setattr(panel, "_get_client", lambda: FakeClient())
    try:
        panel._choose_command("led_on")
        preview = json.loads(panel.preview.toPlainText())
        assert preview["id"] == 1
        assert preview["token"] == "********"
        assert "test-token-123456" not in panel.preview.toPlainText()
        panel._send()
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if panel.thread is None else None)
        timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        timer.stop()
        assert panel.thread is None
        assert sent[0] == (iccid, 3000, "SEND_NOW", {"cmd": "led_on", "id": 1, "token": "test-token-123456"})
        history = store.history()[0]
        assert history[6] == "request123"
        assert history[8:] == ("HTTP 202 aceptado", "Sin confirmar", "")
        assert "test-token-123456" not in str(history)
        assert app is QApplication.instance()
    finally:
        panel.close()


@pytest.fixture
def panel_setup(tmp_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    configure_qt_plugin_paths()
    app = QApplication.instance() or QApplication([])
    store = ControllerStore(tmp_path / "controller.sqlite3")
    iccid = "1234567890123456789"
    store.save_device(iccid, "Example", "LilyGO", 3000, "")
    monkeypatch.setattr(panel_module, "ControllerStore", lambda: store)
    monkeypatch.setattr(panel_module.keyring, "get_password", lambda *_args: "test-token-123456")
    panel = panel_module.ControllerPanel(lambda: ApiCredentials("client", "secret"))
    errors = []
    monkeypatch.setattr(panel, "_show_error", errors.append)
    yield panel, store, iccid, errors, app
    panel.close()


def test_cloud_device_selection_handles_labels_and_existing_local_data(panel_setup):
    panel, store, iccid, _, _ = panel_setup
    other = "1234567890123456788"
    panel.set_cloud_devices([(iccid, "Same"), (other, "Same")])
    assert panel.cloud_devices.count() == 3
    assert iccid in panel.cloud_devices.itemText(1)
    assert other in panel.cloud_devices.itemText(2)
    panel.cloud_devices.setCurrentIndex(2)
    assert panel.iccid.text() == other
    assert panel.name.text() == "Same"
    assert panel.token.text() == ""
    panel.cloud_devices.setCurrentIndex(1)
    assert panel.iccid.text() == iccid
    assert panel.token.text() == "test-token-123456"
    assert len(store.devices()) == 1


def test_custom_command_preserves_fields_and_advances_id(panel_setup, monkeypatch):
    panel, store, iccid, errors, _ = panel_setup
    panel.edit_command_check.setChecked(True)
    data = {"cmd": "custom", "id": 41, "token": "********", "values": [1, 2]}
    panel.preview.setPlainText(json.dumps(data))
    calls = []

    class FakeClient:
        def send(self, device_id, port, mode, payload):
            calls.append(json.loads(payload))
            return "request123"

    monkeypatch.setattr(panel, "_get_client", lambda: FakeClient())
    panel._send()
    assert panel.thread is not None
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if panel.thread is None else None)
    poll.start()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    poll.stop()
    assert panel.thread is None
    assert calls == [{**data, "token": "test-token-123456"}]
    assert store.next_id(iccid) == 42
    assert store.history()[0][4:6] == ("Personalizado", 41)
    assert not errors


def test_custom_command_rejects_repeated_id_and_invalid_json(panel_setup, monkeypatch):
    panel, store, iccid, errors, _ = panel_setup
    monkeypatch.setattr(panel, "_get_client", lambda: object())
    store.next_id(iccid)
    panel.edit_command_check.setChecked(True)
    panel.preview.setPlainText('{"cmd":"led_on","id":1,"token":"********"}')
    panel._send()
    assert "reservado" in errors[-1]
    assert not store.history()
    panel.preview.setPlainText("{")
    panel._send()
    assert "JSON válido" in errors[-1]


def test_custom_reboot_requests_confirmation(panel_setup, monkeypatch):
    panel, store, _, _, _ = panel_setup
    panel.edit_command_check.setChecked(True)
    panel.preview.setPlainText('{"cmd":"reboot","id":"Se asignará al enviar","token":"********"}')
    prompts = []
    monkeypatch.setattr(
        panel_module.QMessageBox, "question",
        lambda *_args: prompts.append(True) or panel_module.QMessageBox.StandardButton.No,
    )
    panel._send()
    assert prompts == [True]
    assert not store.history()


def test_buzzer_uses_fixed_500_ms(panel_setup):
    panel, _, _, _, _ = panel_setup
    panel._choose_command("buzzer")
    assert json.loads(panel.preview.toPlainText())["duration_ms"] == 500


def test_history_columns_fit_content_and_click_copies_action(panel_setup, monkeypatch):
    panel, store, iccid, _, app = panel_setup
    first = store.record("2026-11-11", iccid, "Dispositivo de prueba", "led_on", 1, "SEND_NOW")
    store.update_record(first, "HTTP 202 aceptado", "action-123")
    store.record("2026-11-11", iccid, "Otro dispositivo", "led_off", 2, "SEND_NOW")
    panel._refresh_history()
    panel.resize(1500, 1200)
    panel.show()
    app.processEvents()

    table = panel.history_table
    header = table.horizontalHeader()
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(6) == QHeaderView.ResizeMode.ResizeToContents
    assert table.selectionBehavior() == QTableWidget.SelectionBehavior.SelectRows
    for column in (1, 6):
        item = table.item(1, column)
        assert item is not None
        assert table.columnWidth(column) >= table.fontMetrics().horizontalAdvance(item.text())

    item = table.item(1, 0)
    assert item is not None
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(item).center())
    assert panel.action_id.text() == "action-123"
    assert table.currentRow() == 1

    queried = []

    class FakeClient:
        def status(self, action_id):
            queried.append(action_id)
            return {"status": "SUCCEEDED"}

    monkeypatch.setattr(panel, "_get_client", lambda: FakeClient())
    panel._status()
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if panel.thread is None else None)
    poll.start()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    poll.stop()
    assert panel.thread is None
    assert queried == ["action-123"]
    assert store.history()[1][8] == "SUCCEEDED"
    assert store.history()[1][9] == "Sin confirmar"

    item_without_action = table.item(0, 2)
    assert item_without_action is not None
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(item_without_action).center())
    assert panel.action_id.text() == ""


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-11-13T11:47:03+01:00", "2026-11-13T11:47:03"),
    ("2026-11-13T10:47:03-05:30", "2026-11-13T10:47:03"),
    ("2026-11-13T11:47:19.300Z", "2026-11-13T11:47:19.300"),
    ("2026-11-13T11:47:19.300", "2026-11-13T11:47:19.300"),
    ("", ""),
])
def test_history_date_display_preserves_wall_time(value, expected):
    assert panel_module._display_history_date(value) == expected


def test_history_table_copy_and_csv_hide_offsets_and_uncertainty(panel_setup, monkeypatch, tmp_path):
    panel, store, iccid, _, app = panel_setup
    store.record("2026-11-13T11:47:03+01:00", iccid, "Device", "led_toggle", 75, "SEND_NOW")
    ack = parse_cloud_message({
        "deviceId": iccid, "time": "2026-11-13T11:47:19.300", "protocol": "UDP",
        "payload": '{"event":"command_ack","cmd_id":75,"cmd":"led_toggle","status":"executed"}',
    })
    store.correlate_acks([ack])
    panel._refresh_history()
    assert panel.history_table.item(0, 0).text() == "2026-11-13T11:47:03"
    assert panel.history_table.item(0, 0).toolTip() == "2026-11-13T11:47:03+01:00"
    assert panel.history_table.item(0, 7).text() == "Ejecutado según dispositivo"
    assert panel.history_table.item(0, 8).text() == "2026-11-13T11:47:19.300"
    panel._copy()
    copied = app.clipboard().text().split("\t")
    assert copied[0] == "2026-11-13T11:47:03"
    assert copied[8:] == ["Ejecutado según dispositivo", "2026-11-13T11:47:19.300"]
    path = tmp_path / "history.csv"
    monkeypatch.setattr(panel_module.QFileDialog, "getSaveFileName", lambda *_args: (str(path), "CSV"))
    panel._export()
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows[1] == copied
    assert store.history()[0][1] == "2026-11-13T11:47:03+01:00"
    assert store.history()[0][9] == "Ejecutado según dispositivo (fecha sin zona)"


def test_custom_placeholder_id_and_literal_token_are_sent(panel_setup, monkeypatch):
    panel, store, iccid, errors, _ = panel_setup
    panel.edit_command_check.setChecked(True)
    panel.preview.setPlainText(json.dumps({
        "cmd": "led_on", "id": "Se asignará al enviar", "token": "literal-token-12345"
    }))
    sent = []

    class FakeClient:
        def send(self, device_id, port, mode, payload):
            sent.append(json.loads(payload))
            return "request123"

    monkeypatch.setattr(panel, "_get_client", lambda: FakeClient())
    panel._send()
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: loop.quit() if panel.thread is None else None)
    poll.start()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    poll.stop()
    assert panel.thread is None
    assert sent == [{"cmd": "led_on", "id": 1, "token": "literal-token-12345"}]
    assert store.next_id(iccid) == 2
    assert "literal-token-12345" not in str(store.history())
    assert not errors
