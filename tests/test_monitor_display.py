from datetime import datetime
import json
import os

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication

import tdserial_iot.main_window as main_window_module
import tdserial_iot.onence_historian as onence_historian_module
from tdserial_iot.app_config import DEFAULT_CONFIG
from tdserial_iot.command_queue import AtCommand, InteractionPlan
from tdserial_iot.main import configure_qt_plugin_paths
from tdserial_iot.main_window import MainWindow
from tdserial_iot.onence_api import AccessToken


@pytest.fixture(scope="module")
def qt_app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    configure_qt_plugin_paths()
    app = QApplication.instance() or QApplication([])
    yield app


def test_display_checkbox_redraws_existing_records(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    saved_config: list[dict[str, object]] = []
    monkeypatch.setattr(
        main_window_module,
        "save_config",
        lambda config: saved_config.append(config.copy()),
    )
    window = MainWindow()
    try:
        connection_layout = window.initialize_at_check.parentWidget().layout()
        initialize_position = connection_layout.getItemPosition(
            connection_layout.indexOf(window.initialize_at_check)
        )
        connect_position = connection_layout.getItemPosition(
            connection_layout.indexOf(window.connect_button)
        )
        assert initialize_position[0] == connect_position[0]
        assert initialize_position[1] < connect_position[1]
        assert window.autoscroll_check.objectName() == "toolbarCheck"
        assert window.show_details_check.objectName() == "toolbarCheck"
        assert window.onence_auto_check.objectName() == "toolbarCheck"
        assert "QCheckBox#toolbarCheck" in window.styleSheet()
        assert "QTabBar::tab {" in window.styleSheet()
        assert "QTabBar::tab:selected" in window.styleSheet()
        assert not window.show_details_check.isChecked()
        window._clear_console()
        window._add_event("TX", b"AT\r")
        plain = window.console.toPlainText()
        assert "AT" in plain
        assert "␍" not in plain
        assert "bytes" not in plain

        window.show_details_check.setChecked(True)
        assert "AT␍ (3 bytes)" in window.console.toPlainText()

        window.show_details_check.setChecked(False)
        plain_again = window.console.toPlainText()
        assert "AT" in plain_again
        assert "␍" not in plain_again
        assert "bytes" not in plain_again
    finally:
        window.close()
    assert saved_config[-1]["show_special_characters_and_size"] is False


def test_multiline_input_sends_one_command_until_terminal_response(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window.command_input.setPlainText("ATI\nAT+CPIN?\nAT+CSQ\n")
        window._update_byte_count()
        assert window.byte_count_label.text() == "20 bytes · 3 comando(s)"

        window._send_command()
        assert sent == [b"ATI\r"]
        window._process_rx_line(b"ATI\r\n")
        assert "RX ← ATI" in window.console.toPlainText()
        assert sent == [b"ATI\r"]
        window._process_rx_line(b"+CREG: 1\r\n")
        assert sent == [b"ATI\r"]
        window._process_rx_line(b"OK\r\n")
        assert sent == [b"ATI\r", b"AT+CPIN?\r"]
        window._process_rx_line(b"+CPIN: READY\r\n")
        assert sent == [b"ATI\r", b"AT+CPIN?\r"]
        window._process_rx_line(b"OK\r\n")
        assert sent == [b"ATI\r", b"AT+CPIN?\r", b"AT+CSQ\r"]
    finally:
        window.close()


def test_sequence_controls_show_only_for_multiline_sequence(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        assert window.sequence_controls_widget.isHidden()
        window.command_input.setPlainText("ATI")
        assert window.sequence_controls_widget.isHidden()
        window.command_input.setPlainText("ATI\nAT+CSQ")
        assert not window.sequence_controls_widget.isHidden()

        window._is_connected = True
        window._send_command()
        assert sent == [b"ATI\r"]
        assert not window.sequence_controls_widget.isHidden()
        window._process_rx_line(b"OK\r\n")
        assert sent == [b"ATI\r", b"AT+CSQ\r"]
        assert not window.sequence_controls_widget.isHidden()
        window._process_rx_line(b"OK\r\n")
        assert window.sequence_controls_widget.isHidden()
    finally:
        window.close()


def test_single_command_timeout_shows_resume_and_cancel_controls(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    monkeypatch.setattr(window, "_write_queued_command", lambda command: None)
    try:
        window._is_connected = True
        window.command_input.setPlainText("!")
        window._send_command()
        window._command_timeout_timer.stop()

        window._on_command_timeout()

        assert window._command_queue.paused
        assert not window.sequence_controls_widget.isHidden()
        assert window.resume_queue_button.isEnabled()
        assert window.cancel_queue_button.isEnabled()

        window._cancel_command_queue()
        assert not window._command_queue.paused
        assert window.sequence_controls_widget.isHidden()
        assert window.send_button.isEnabled()
    finally:
        window.close()


def test_diagnostic_tab_uses_shared_queue_and_updates_sim_card(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        assert window.tabs.tabText(1) == "Diagnóstico IoT"
        assert window.tabs.tabText(3) == "1NCE Cloud"
        window._on_connection_changed(True)
        window._start_diagnostic(("sim",))
        assert sent == [b"AT+CPIN?\r"]
        window._process_rx_line(b"+CPIN: READY\r\n")
        window._process_rx_line(b"OK\r\n")
        assert "SIM preparada" in window._diagnostic_fields["sim"].text()
        assert window._diagnostic_manual_remaining == 0
        assert "finalizado" in window.diagnostic_progress_label.text().casefold()
        assert "Último diagnóstico:" in window.last_diagnostic_label.text()
    finally:
        window.close()


def test_automatic_monitor_updates_timestamp_only_after_successful_full_round(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    previous_timestamp = datetime(2000, 1, 1, 0, 0, 0)
    try:
        window._is_connected = True
        window._last_diagnostic_at = previous_timestamp
        window.automatic_monitor_check.setChecked(True)
        window._run_automatic_monitor()
        assert len(sent) == 1

        for index in range(len(main_window_module.MONITOR_KEYS)):
            window._process_rx_line(b"OK\r\n")
            if index < len(main_window_module.MONITOR_KEYS) - 1:
                assert window._last_diagnostic_at is previous_timestamp

        assert window._last_diagnostic_at is not previous_timestamp
        assert "Último diagnóstico:" in window.last_diagnostic_label.text()
        assert window.diagnostic_progress_label.text() == "Monitorización actualizada"
    finally:
        window.close()


def test_automatic_monitor_does_not_update_timestamp_after_failed_round(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    previous_timestamp = datetime(2000, 1, 1, 0, 0, 0)
    try:
        window._is_connected = True
        window._last_diagnostic_at = previous_timestamp
        window.automatic_monitor_check.setChecked(True)
        window._run_automatic_monitor()

        for index in range(len(main_window_module.MONITOR_KEYS)):
            window._process_rx_line(
                b"ERROR\r\n" if index == 0 else b"OK\r\n"
            )

        assert window._last_diagnostic_at is previous_timestamp
        assert window.diagnostic_progress_label.text() == (
            "Monitorización finalizada con consultas fallidas"
        )
    finally:
        window.close()


def test_cancelled_automatic_round_does_not_update_timestamp(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    previous_timestamp = datetime(2000, 1, 1, 0, 0, 0)
    try:
        window._is_connected = True
        window._last_diagnostic_at = previous_timestamp
        window.automatic_monitor_check.setChecked(True)
        window._run_automatic_monitor()
        window._cancel_command_queue()
        window._process_rx_line(b"OK\r\n")

        assert window._last_diagnostic_at is previous_timestamp
    finally:
        window.close()


def test_onence_authentication_runs_without_serial_connection(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    monkeypatch.setattr(main_window_module, "load_api_credentials", lambda: None)
    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator,
        "get_access_token",
        lambda self, *, force_refresh=False: AccessToken(
            "test-token", "Bearer", 3600
        ),
    )
    window = MainWindow()
    try:
        window.onence_client_id_edit.setText("test-client")
        window.onence_client_secret_edit.setText("test-secret")
        assert not window._is_connected
        window._test_onence_connection()

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(10)
        poll_timer.timeout.connect(
            lambda: loop.quit() if window.onence_test_button.isEnabled() else None
        )
        poll_timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll_timer.stop()

        assert window.onence_test_button.isEnabled()
        assert window.onence_auth_status_label.text() == (
            "Autenticación correcta; token disponible."
        )
        assert not window._is_connected
    finally:
        window.close()


def test_onence_history_query_runs_without_com_and_shows_full_iccid(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    token_refreshes = []
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    monkeypatch.setattr(main_window_module, "load_api_credentials", lambda: None)

    def get_test_token(self, *, force_refresh=False):
        token_refreshes.append(force_refresh)
        return AccessToken("test-token", "Bearer", 3600)

    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator,
        "get_access_token",
        get_test_token,
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "items": [
                        {
                            "deviceId": "001234567890",
                            "iccid": "001234567890",
                            "time": "2026-11-09T19:39:00Z",
                            "protocol": "UDP",
                            "payload": '{"temp":21.3}',
                        }
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        onence_historian_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    window = MainWindow()
    try:
        window._is_connected = False
        window.onence_iccid_edit.setText("001234567890")
        window.onence_device_mode_combo.setCurrentText("ICCID manual")
        window.onence_range_combo.setCurrentText("Últimos mensajes")
        window._onence_authenticator = main_window_module.OneNCEAuthenticator(
            main_window_module.ApiCredentials("test-client", "test-secret")
        )
        window._start_onence_history_query()

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(10)
        poll_timer.timeout.connect(
            lambda: loop.quit()
            if window.onence_query_button.isEnabled()
            else None
        )
        poll_timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll_timer.stop()

        assert window.onence_query_button.isEnabled()
        assert window.onence_history_table.rowCount() == 1
        assert window.onence_history_table.item(0, 2).text() == "001234567890"
        assert window.onence_history_table.item(0, 4).text() == '{"temp":21.3}'
        assert (
            window.onence_history_table.horizontalHeaderItem(8).text() == "temp"
        )
        assert window.onence_history_table.item(0, 8).text() == "21.3"
        assert window.onence_history_table.columnWidth(2) >= 190
        assert window.onence_history_table.columnWidth(8) >= 100
        assert window.onence_history_table.minimumHeight() >= 145
        assert (
            window.onence_history_table.horizontalHeader().defaultAlignment()
            == Qt.AlignmentFlag.AlignCenter
        )
        assert all(
            window.onence_history_table.item(0, column).textAlignment()
            == Qt.AlignmentFlag.AlignCenter
            for column in range(window.onence_history_table.columnCount())
        )
        window.onence_history_table.setCurrentCell(0, 0)
        window.onence_payload_view_combo.setCurrentText("JSON")
        assert '"temp": 21.3' in window.onence_message_detail.toPlainText()
        assert "ICCID: 001234567890" in window.onence_message_detail.toPlainText()
        assert token_refreshes[0] is True
        assert not window._is_connected
    finally:
        window.close()


def test_onence_last_seven_days_preset_uses_fresh_safe_range(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator,
        "get_access_token",
        lambda self, *, force_refresh=False: AccessToken("fresh-token", "Bearer", 3600),
    )
    requests = []

    def get_history_page(self, device_id, **kwargs):
        requests.append((device_id, kwargs))
        return main_window_module.HistoryPage((), None)

    monkeypatch.setattr(
        main_window_module.OneNCEHistorianClient,
        "get_history_page",
        get_history_page,
    )
    window = MainWindow()
    try:
        window.onence_client_id_edit.setText("test-client")
        window.onence_client_secret_edit.setText("test-secret")
        window.onence_iccid_edit.setText("0012345678901234567")
        window.onence_device_mode_combo.setCurrentText("ICCID manual")
        window.onence_range_combo.setCurrentText("Últimos 7 días")
        window._start_onence_history_query()

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(10)
        poll_timer.timeout.connect(
            lambda: loop.quit()
            if window.onence_query_button.isEnabled() and requests
            else None
        )
        poll_timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll_timer.stop()

        assert requests
        device_id, query = requests[0]
        assert device_id == "0012345678901234567"
        assert query["start_datetime"] is not None
        assert query["end_datetime"] is not None
        query_span = (
            query["end_datetime"] - query["start_datetime"]
        ).total_seconds()
        assert 6 * 24 * 60 * 60 < query_span < 7 * 24 * 60 * 60
        assert "supera la retención" not in window.onence_history_status_label.text()
    finally:
        window.close()


@pytest.mark.parametrize(
    "period",
    [
        "Últimos mensajes",
        "Última hora",
        "Últimas 24 horas",
        "Últimos 7 días",
        "Personalizado",
    ],
)
def test_onence_history_refreshes_until_at_query_time_except_custom(
    qt_app, monkeypatch, period
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator,
        "get_access_token",
        lambda self, *, force_refresh=False: AccessToken("fresh-token", "Bearer", 3600),
    )
    requests = []

    def get_history_page(self, device_id, **kwargs):
        requests.append(kwargs)
        return main_window_module.HistoryPage((), None)

    monkeypatch.setattr(
        main_window_module.OneNCEHistorianClient,
        "get_history_page",
        get_history_page,
    )
    window = MainWindow()
    try:
        window.onence_client_id_edit.setText("test-client")
        window.onence_client_secret_edit.setText("test-secret")
        window.onence_iccid_edit.setText("0012345678901234567")
        window.onence_device_mode_combo.setCurrentText("ICCID manual")
        window.onence_range_combo.setCurrentText(period)
        window.onence_end_datetime.setDateTime(
            window.onence_end_datetime.dateTime().addSecs(-300)
        )
        if period == "Personalizado":
            window.onence_start_datetime.setDateTime(
                window.onence_end_datetime.dateTime().addSecs(-3600)
            )
        custom_end = window._local_datetime(window.onence_end_datetime)
        query_started_at = datetime.now().astimezone()

        window._start_onence_history_query()

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(10)
        poll_timer.timeout.connect(
            lambda: loop.quit()
            if window.onence_query_button.isEnabled() and requests
            else None
        )
        poll_timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll_timer.stop()

        assert requests
        query_end = requests[0]["end_datetime"]
        if period == "Últimos mensajes":
            assert query_end is None
        elif period == "Personalizado":
            assert query_end == custom_end
        else:
            assert query_end is not None
            assert abs((query_end - query_started_at).total_seconds()) < 5
            assert abs(
                (
                    window._local_datetime(window.onence_end_datetime)
                    - query_started_at
                ).total_seconds()
            ) < 5
    finally:
        window.close()


def test_onence_all_devices_query_does_not_require_manual_iccid(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator,
        "get_access_token",
        lambda self, *, force_refresh=False: AccessToken("fresh-token", "Bearer", 3600),
    )
    requests = []

    def get_all_history_page(self, **kwargs):
        requests.append(kwargs)
        return main_window_module.HistoryPage((), None)

    monkeypatch.setattr(
        main_window_module.OneNCEHistorianClient,
        "get_all_history_page",
        get_all_history_page,
    )
    window = MainWindow()
    try:
        window.onence_client_id_edit.setText("test-client")
        window.onence_client_secret_edit.setText("test-secret")
        window.onence_iccid_edit.clear()
        window.onence_range_combo.setCurrentText("Últimos mensajes")
        assert window.onence_device_mode_combo.currentText() == "Todos los dispositivos"
        window._start_onence_history_query()

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(10)
        poll_timer.timeout.connect(
            lambda: loop.quit()
            if window.onence_query_button.isEnabled() and requests
            else None
        )
        poll_timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll_timer.stop()

        assert requests
        assert requests[0]["protocol"] == "UDP"
        assert window.onence_history_table.rowCount() == 0
        assert "no hay mensajes" in window.onence_history_status_label.text().lower()
    finally:
        window.close()


def test_onence_device_selector_keeps_duplicate_labels_and_iccid_fallback(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    try:
        devices = (
            main_window_module.OneNCEDevice("iccid-1", "Equipo"),
            main_window_module.OneNCEDevice("iccid-2", "Equipo"),
            main_window_module.OneNCEDevice("iccid-3"),
        )
        window._on_onence_devices_completed(True, devices, "")
        window.onence_device_mode_combo.setCurrentText("Seleccionar dispositivo")

        assert window.onence_device_combo.count() == 3
        assert window.onence_device_combo.itemText(0) == "Equipo — iccid-1"
        assert window.onence_device_combo.itemText(1) == "Equipo — iccid-2"
        assert window.onence_device_combo.itemData(0) == "iccid-1"
        assert window.onence_device_combo.itemData(1) == "iccid-2"
        assert window.onence_device_combo.itemText(2) == "iccid-3"
        assert window.controller_panel is not None
        assert window.controller_panel.cloud_devices.count() == 4
        assert window.controller_panel.cloud_devices.itemData(1) == "iccid-1"
        assert window.controller_panel.cloud_devices.itemData(2) == "iccid-2"
        assert window.controller_panel.cloud_devices.itemText(3) == "iccid-3"

        window._cloud_messages = [
            onence_historian_module.parse_cloud_message(
                {
                    "iccid": "iccid-1",
                    "time": "2026-11-09T19:39:00Z",
                    "protocol": "UDP",
                    "payload": '{"temp":21}',
                }
            ),
            onence_historian_module.parse_cloud_message(
                {
                    "iccid": "iccid-3",
                    "time": "2026-11-09T19:40:00Z",
                    "protocol": "UDP",
                    "payload": "plain",
                }
            ),
        ]
        window._render_onence_history_table()
        headers = [
            window.onence_history_table.horizontalHeaderItem(index).text()
            for index in range(7)
        ]
        assert headers == [
            "Fecha y hora",
            "LABEL",
            "ICCID",
            "Protocolo",
            "Contenido",
            "Tamaño",
            "Estado de decodificación",
        ]
        assert window.onence_history_table.item(0, 1).text() == "Equipo"
        assert window.onence_history_table.item(0, 2).text() == "iccid-1"
        assert window.onence_history_table.item(1, 1).text() == "iccid-3"
        assert window.onence_history_table.item(0, 6).text().startswith("JSON válido")
    finally:
        window.close()


def test_historian_results_update_controller_history_and_table(qt_app, monkeypatch, tmp_path):
    from tdserial_iot.controller_core import ControllerStore

    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    window = MainWindow()
    try:
        panel = window.controller_panel
        assert panel is not None
        panel.store = ControllerStore(tmp_path / "controller.sqlite3")
        iccid = "1234567890123456789"
        panel.store.record("2026-11-13T10:00:00+00:00", iccid, "Device", "led_on", 7, "SEND_NOW")
        ack = onence_historian_module.parse_cloud_message({
            "iccid": iccid, "time": "2026-11-13T10:01:00Z", "protocol": "UDP",
            "payload": '{"event":"command_ack","cmd_id":7,"cmd":"led_on","status":"executed"}',
        })
        window._on_onence_history_completed(True, onence_historian_module.HistoryPage((ack,), None), "")
        assert panel.history_table.item(0, 7).text() == "Ejecutado según dispositivo"
        assert panel.history_table.item(0, 8).text() == "2026-11-13T10:01:00"
        assert panel.history_table.item(0, 8).toolTip() == "2026-11-13T10:01:00Z"
        window._on_onence_history_completed(True, onence_historian_module.HistoryPage((), None), "")
        assert panel.history_table.item(0, 7).text() == "Ejecutado según dispositivo"
        window._cloud_query_append = True
        conflicting = onence_historian_module.parse_cloud_message({
            "iccid": iccid, "time": "2026-11-13T10:02:00Z", "protocol": "UDP",
            "payload": '{"event":"command_ack","cmd_id":7,"cmd":"led_on","status":"accepted"}',
        })
        window._on_onence_history_completed(
            True, onence_historian_module.HistoryPage((conflicting,), None), ""
        )
        assert panel.history_table.item(0, 7).text() == "Correlación ambigua"
    finally:
        window.close()


def test_historian_reports_correlation_database_error(qt_app, monkeypatch, tmp_path):
    import sqlite3
    from tdserial_iot.controller_core import ControllerStore

    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    window = MainWindow()
    try:
        panel = window.controller_panel
        assert panel is not None
        panel.store = ControllerStore(tmp_path / "controller.sqlite3")

        def fail(_messages):
            raise sqlite3.OperationalError("database unavailable")

        monkeypatch.setattr(panel.store, "correlate_acks", fail)
        window._on_onence_history_completed(True, onence_historian_module.HistoryPage((), None), "")
        assert "no se pudo guardar la correlación" in window.onence_history_status_label.text()
    finally:
        window.close()


def test_historian_base64_ack_without_timezone_updates_controller_table(qt_app, monkeypatch, tmp_path):
    from tdserial_iot.controller_core import ControllerStore

    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    window = MainWindow()
    try:
        panel = window.controller_panel
        assert panel is not None
        panel.store = ControllerStore(tmp_path / "controller.sqlite3")
        iccid = "1234567890123456789"
        panel.store.record("2026-11-13T11:47:03+01:00", iccid, "Device", "led_toggle", 75, "SEND_NOW")
        ack = onence_historian_module.parse_cloud_message({
            "iccid": iccid, "deviceId": iccid, "time": "2026-11-13T11:47:19.300", "protocol": "UDP",
            "payload": "eyJpZCI6InRkc2ltNzA4MC0wMSIsImV2ZW50IjoiY29tbWFuZF9hY2siLCJjbWRfaWQiOjc1LCJzdGF0dXMiOiJleGVjdXRlZCIsImNtZCI6ImxlZF90b2dnbGUiLCJsZWQiOnRydWV9",
        })
        window._on_onence_history_completed(True, onence_historian_module.HistoryPage((ack,), None), "")
        assert panel.history_table.item(0, 7).text() == "Ejecutado según dispositivo"
        assert panel.history_table.item(0, 8).text() == "2026-11-13T11:47:19.300"
    finally:
        window.close()


def test_controller_refresh_button_loads_cloud_devices_without_serial(qt_app, monkeypatch):
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator, "get_access_token",
        lambda self, *, force_refresh=False: AccessToken("fresh-token", "Bearer", 3600),
    )
    monkeypatch.setattr(
        main_window_module.OneNCEHistorianClient, "get_device_iccids",
        lambda self: ("1234567890123456789",),
    )
    monkeypatch.setattr(
        main_window_module.OneNCEHistorianClient, "get_sim_labels",
        lambda self: {"1234567890123456789": "Equipo"},
    )
    window = MainWindow()
    try:
        window.onence_client_id_edit.setText("test-client")
        window.onence_client_secret_edit.setText("test-secret")
        assert window.controller_panel is not None
        window.controller_panel.refresh_devices_button.click()
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(
            lambda: loop.quit()
            if window._onence_devices_thread is None
            and window.controller_panel.cloud_devices.count() == 2
            else None
        )
        poll.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll.stop()
        assert window.controller_panel.cloud_devices.itemData(1) == "1234567890123456789"
        assert "Equipo" in window.controller_panel.cloud_devices.itemText(1)
        assert not window._is_connected
    finally:
        window.close()


def test_onence_auto_refresh_updates_last_query_time(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    try:
        window.onence_auto_interval_combo.setCurrentText("30 segundos")
        window.onence_auto_check.setChecked(True)
        assert window.onence_auto_check.isChecked()

        window._on_onence_history_completed(
            True,
            main_window_module.HistoryPage((), None),
            "",
        )

        assert window.onence_last_query_label.text().startswith("Última consulta: ")
        assert "N/D" not in window.onence_last_query_label.text()
        assert window._cloud_auto_timer.isActive()
    finally:
        window.close()


def test_onence_auto_timer_runs_history_query_and_refreshes_timestamp(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    monkeypatch.setattr(
        main_window_module.OneNCEAuthenticator,
        "get_access_token",
        lambda self, *, force_refresh=False: AccessToken("fresh-token", "Bearer", 3600),
    )
    requests = []

    def get_history_page(self, device_id, **kwargs):
        requests.append((device_id, kwargs))
        return main_window_module.HistoryPage((), None)

    monkeypatch.setattr(
        main_window_module.OneNCEHistorianClient,
        "get_history_page",
        get_history_page,
    )
    window = MainWindow()
    try:
        window.onence_client_id_edit.setText("test-client")
        window.onence_client_secret_edit.setText("test-secret")
        window.onence_iccid_edit.setText("0012345678901234567")
        window.onence_device_mode_combo.setCurrentText("ICCID manual")
        window.onence_range_combo.setCurrentText("Última hora")
        window.onence_auto_interval_combo.setCurrentText("30 segundos")
        window.onence_auto_check.setChecked(True)
        window._cloud_auto_timer.start(20)

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(10)
        poll_timer.timeout.connect(
            lambda: loop.quit()
            if requests
            and window.onence_query_button.isEnabled()
            and window._onence_history_thread is None
            and window.onence_last_query_label.text() != "Última consulta: N/D"
            and window._cloud_auto_timer.isActive()
            else None
        )
        poll_timer.start()
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        poll_timer.stop()

        assert len(requests) == 1
        assert requests[0][0] == "0012345678901234567"
        assert window.onence_last_query_label.text().startswith("Última consulta: ")
        assert window.onence_last_query_label.text() != "Última consulta: N/D"
        assert window._cloud_auto_timer.isActive(), (
            window.onence_history_status_label.text(),
            window.onence_auto_check.isChecked(),
            window.onence_auto_interval_combo.currentText(),
        )
        assert window._cloud_auto_timer.remainingTime() > 0
    finally:
        window.close()


def test_direct_send_transmits_single_character_without_at_terminator_and_displays_rx(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    transmitted: list[bytes] = []
    try:
        window.write_requested.disconnect()
        window.write_requested.connect(transmitted.append)
        window._is_connected = True
        window.command_input.setPlainText("!")
        window.direct_send_check.setChecked(True)
        window._update_queue_controls()
        window._send_command()

        assert transmitted == [b"!"]
        assert window.command_input.toPlainText() == ""

        window._on_transmitted(transmitted[0])
        window._on_received(b"ready\r\n")
        console = window.console.toPlainText()
        assert "TX → !" in console
        assert "RX ← ready" in console
    finally:
        window.close()


def test_fragmented_prompt_sends_exact_payload_immediately_without_finishing_queue(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    window.write_requested.connect(sent.append)
    try:
        command = AtCommand(
            b"AT+CASEND=0,5,5000\r",
            timeout_ms=1000,
            interaction=InteractionPlan(
                prompt=b">",
                payload=b"\x00\n\xffab",
                response_timeout_ms=2000,
            ),
        )
        first = window._command_queue.enqueue([command])
        assert first is command
        window._write_queued_command(command.data)

        window._on_received(b"\r\n>")
        assert sent[-1] == b"\x00\n\xffab"
        assert window._command_queue.active_spec is command
        assert window._command_timeout_timer.interval() == 2000
        window._process_rx_line(b"OK\r\n")
        assert window._command_queue.active_spec is None
    finally:
        window.close()


def test_context_activation_skips_configuration_when_apn_is_already_correct(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window._start_iot_flow("activate")
        assert sent == [b"AT+CNCFG?\r"]
        window._process_rx_line(b'+CNCFG: 0,1,"iot.1nce.net","","",0\r\n')
        window._process_rx_line(b"OK\r\n")
        assert sent[-1] == b"AT+CNACT?\r"
        window._process_rx_line(b'+CNACT: 0,0,"0.0.0.0"\r\n')
        window._process_rx_line(b"OK\r\n")
        assert sent[-1] == b"AT+CNACT=0,1\r", (
            f"flow={window._iot_flow_action}; "
            f"responses={window._iot_flow_responses}; "
            f"active={window._command_queue.active_spec}; "
            f"pending={window._command_queue.pending_count}; "
            f"results={window.iot_results.toPlainText()}"
        )
        window._process_rx_line(b"OK\r\n")
        assert sent[-1] == b"AT+CNACT?\r"
        window._process_rx_line(b'+CNACT: 0,1,"10.0.0.1"\r\n')
        window._process_rx_line(b"OK\r\n")
        assert window._iot_flow_action is None
        assert "activado y verificado" in window.iot_results.toPlainText()
        assert b'AT+CNCFG=0,1,"iot.1nce.net"\r' not in sent
    finally:
        window.close()


def test_udp_send_waits_for_prompt_and_reports_modem_ok_without_remote_claim(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    window.write_requested.connect(sent.append)
    try:
        window._is_connected = True
        window.iot_format_combo.setCurrentText("ASCII / UTF-8")
        window.iot_payload_edit.setPlainText("ñ")
        window._start_iot_flow("send")
        assert sent == [b"AT+CEREG?\r"]

        for response, next_command in (
            (b"+CEREG: 0,1\r\n", b"AT+CGATT?\r"),
            (b"+CGATT: 1\r\n", b"AT+CNACT?\r"),
            (b'+CNACT: 0,1,"10.0.0.1"\r\n', b"AT+CASTATE?\r"),
            (b"+CASTATE: 0,1\r\n", b"AT+CASEND=0,2,5000\r"),
        ):
            window._process_rx_line(response)
            window._process_rx_line(b"OK\r\n")
            assert sent[-1] == next_command

        assert b"\xc3\xb1" not in sent
        window._on_received(b"> ")
        assert sent[-1] == "ñ".encode("utf-8")
        window._on_transmitted("ñ".encode("utf-8"))
        window._process_rx_line(b"OK\r\n")
        assert window._iot_flow_action is None
        result = window.iot_results.toPlainText()
        assert "el módem respondió OK" in result
        assert "recepción remota UDP no está confirmada" in result
        assert window._iot_history[-1].payload_size == 2
    finally:
        window.close()


def test_carecv_preserves_binary_payload_and_uses_reported_length(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window._start_iot_flow("receive")
        assert sent == [b"AT+CEREG?\r"]

        for response, next_command in (
            (b"+CEREG: 0,1\r\n", b"AT+CGATT?\r"),
            (b"+CGATT: 1\r\n", b"AT+CNACT?\r"),
            (b'+CNACT: 0,1,"10.0.0.1"\r\n', b"AT+CASTATE?\r"),
            (b"+CASTATE: 0,1\r\n", b"AT+CARECV=0,1460\r"),
        ):
            window._process_rx_line(response)
            window._process_rx_line(b"OK\r\n")
            assert sent[-1] == next_command

        payload = b"\x00A\r\n\xff"
        window._on_received(
            b"AT+CARECV=0,1460\r\n+CARECV: 5\r\n"
            + payload
            + b"\r\nOK\r\n"
        )

        assert window._iot_flow_action is None
        assert bytes(window._iot_received_payload) == payload
        assert any(record[:2] == ("RX", payload) for record in window._records)
        assert repr(payload) in window.iot_results.toPlainText()
    finally:
        window.close()


def test_copy_diagnostic_includes_timestamp_all_fields_and_full_identifiers(
    qt_app, monkeypatch, tmp_path
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    try:
        window._diagnostic_values.update(
            {
                "manufacturer": "SIMCOM",
                "imei": "123456789012345",
                "iccid": "8988211000001234567",
                "modem_info": "SIM7080G",
                "access_technology": "LTE NB-S1 (IoT)",
                "technology": "LTE NB-IOT",
                "rsrp": "-90 dBm",
                "rsrq": "N/D",
                "sinr": "N/D",
            }
        )
        window._update_derived_diagnostic_values()
        window._render_identity_fields()
        window._last_diagnostic_at = datetime(2026, 11, 9, 16, 10, 23)

        assert not hasattr(window, "reveal_identifiers_check")
        assert window._diagnostic_fields["imei"].text() == "● 123456789012345"
        assert window._diagnostic_fields["iccid"].text() == "● 8988211000001234567"

        window.copy_diagnostic_button.click()
        report = qt_app.clipboard().text()
        assert "Fecha y hora: 09/11/2026 16:10:23" in report
        assert "Fabricante: SIMCOM" in report
        assert "Modelo: N/D" in report
        assert "IMEI: 123456789012345" in report
        assert "ICCID: 8988211000001234567" in report
        assert "Información ATI: SIM7080G" in report
        assert "Tecnología de acceso reportada: LTE NB-S1 (IoT)" in report
        assert "Calidad de señal NB-IoT: Buena (diagnóstico parcial; N/D: RSRQ, SINR)" in report

        diagnostic_path = tmp_path / "diagnostic.json"
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: (str(diagnostic_path), "JSON (*.json)"),
        )
        window._export_diagnostic()
        exported = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        assert exported["diagnostic"]["Calidad de señal NB-IoT"].startswith("Buena")
        assert exported["diagnostic"]["IMEI"] == "123456789012345"
    finally:
        window.close()


def test_signal_quality_label_and_report_follow_cpsi_rat(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    try:
        window._diagnostic_values.update(
            {
                "technology": "LTE CAT-M1",
                "rsrp": "-100 dBm",
                "rsrq": "-10 dB",
                "sinr": "6 dB (calculado desde RSSNR 13)",
                "registration": "Registrado en red local",
            }
        )
        window._update_derived_diagnostic_values()
        assert window._diagnostic_title_labels["signal_quality"].text() == (
            "Calidad de señal LTE-M"
        )
        report = window._diagnostic_report_values()
        assert "Calidad de señal LTE-M" in report
        assert "Calidad de señal NB-IoT" not in report

        window._diagnostic_values["technology"] = "No Service"
        window._update_derived_diagnostic_values()
        assert window._diagnostic_title_labels["signal_quality"].text() == (
            "Calidad de señal"
        )
        assert "Calidad de señal" in window._diagnostic_report_values()
    finally:
        window.close()


def test_automatic_monitor_uses_only_four_queries_and_skips_overlapping_cycle(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._on_connection_changed(True)
        window.automatic_monitor_check.setChecked(True)
        assert sent == [b"AT+CEREG?\r"]
        assert "Monitorización activa" in window.monitor_status_label.text()
        window._run_automatic_monitor()
        assert sent == [b"AT+CEREG?\r"]

        for expected in (
            b"AT+CSQ\r",
            b"AT+CPSI?\r",
            b"AT+CGATT?\r",
        ):
            window._process_rx_line(b"OK\r\n")
            assert sent[-1] == expected
            window._run_automatic_monitor()
            assert sent[-1] == expected
        window._process_rx_line(b"OK\r\n")
        assert not window._auto_monitor_running
        assert len(sent) == 4
        assert window._automatic_monitor_timer.isActive()
        window.automatic_monitor_check.setChecked(False)
        assert not window._automatic_monitor_timer.isActive()
        assert window.monitor_status_label.text() == "Monitorización detenida"
    finally:
        window.close()


def test_fragmented_terminal_and_interleaved_urc_advance_only_after_complete_result(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window.command_input.setPlainText("AT+CSQ\nATI")
        window._send_command()
        assert sent == [b"AT+CSQ\r"]

        window._on_received(b"+CEREG: 1\r\nO")
        window._rx_flush_timer.stop()
        window._flush_incomplete_rx_line()
        assert sent == [b"AT+CSQ\r"]

        window._on_received(b"K\r\n")
        assert sent == [b"AT+CSQ\r", b"ATI\r"]
    finally:
        window.close()


def test_error_policy_stops_by_default_and_resume_sends_next_command(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        assert window.error_policy_combo.currentText() == "Detener"
        window.command_input.setPlainText("AT+CPIN?\nAT+CSQ")
        window._send_command()
        window._process_rx_line(b"+CME ERROR: 10\r\n")
        assert sent == [b"AT+CPIN?\r"]
        assert window._command_queue.paused
        assert window._command_queue.error_count == 1
        assert window._command_queue.pending_count == 1
        assert window.resume_queue_button.isEnabled()

        window._resume_command_queue()
        assert sent == [b"AT+CPIN?\r", b"AT+CSQ\r"]
    finally:
        window.close()


def test_last_error_finishes_sequence_without_locking_new_commands(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    monkeypatch.setattr(window, "_write_queued_command", lambda command: None)
    try:
        window._is_connected = True
        window.command_input.setPlainText("ATI")
        window._send_command()
        window._process_rx_line(b"ERROR\r\n")
        assert not window._command_queue.paused
        assert window.send_button.isEnabled()
        assert "finalizada con errores" in window.queue_progress_label.text()
    finally:
        window.close()


def test_configured_timeout_is_captured_per_command(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    monkeypatch.setattr(window, "_write_queued_command", lambda command: None)
    try:
        window._is_connected = True
        window.command_timeout_spin.setValue(37)
        window.command_input.setPlainText("ATI\nAT+CSQ")
        window._send_command()
        assert window._command_queue.active_spec is not None
        assert window._command_queue.active_spec.timeout_ms == 37_000
    finally:
        window.close()


def test_error_policy_continue_advances_and_timeout_does_not(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window.error_policy_combo.setCurrentText("Continuar")
        window.command_input.setPlainText("AT+CSQ\nATI")
        window._send_command()
        window._process_rx_line(b"ERROR\r\n")
        assert sent == [b"AT+CSQ\r", b"ATI\r"]
        window._on_command_timeout()
        assert window._command_queue.paused
        assert window.resume_queue_button.isEnabled()
        assert len(sent) == 2
    finally:
        window.close()


def test_error_policy_ask_waits_for_explicit_non_modal_decision(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window.error_policy_combo.setCurrentText("Preguntar")
        window.command_input.setPlainText("ATI\nAT+CSQ")
        window._send_command()
        window._process_rx_line(b"ERROR\r\n")
        dialog = window._error_dialog
        assert dialog is not None
        assert sent == [b"ATI\r"]
        assert window._command_queue.paused

        continue_button = next(button for button in dialog.buttons() if button.text() == "Continuar")
        continue_button.click()
        assert sent == [b"ATI\r", b"AT+CSQ\r"]
    finally:
        window.close()


def test_cancel_sequence_keeps_current_command_but_cancels_pending(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._is_connected = True
        window.command_input.setPlainText("ATI\nAT+CSQ")
        window._send_command()
        window._cancel_command_queue()
        assert sent == [b"ATI\r"]
        assert window._command_queue.pending_count == 0
        assert window._command_queue.active == b"ATI\r"
        window._process_rx_line(b"OK\r\n")
        assert sent == [b"ATI\r"]
        assert "Secuencia cancelada" in window.queue_progress_label.text()
    finally:
        window.close()


def test_command_input_expands_for_multiline_and_collapses_when_cleared(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    try:
        input_box = window.command_input
        single_line_height = input_box.height()
        input_box.setPlainText("ATI\nAT+CSQ\nAT+COPS?")
        multiline_height = input_box.height()

        assert multiline_height > single_line_height
        assert multiline_height <= input_box.fontMetrics().lineSpacing() * 5 + 20

        input_box.clear()
        assert input_box.height() <= input_box.fontMetrics().lineSpacing() + 20
        assert input_box.height() < multiline_height
    finally:
        window.close()


def test_echo_checkbox_sends_ate1_and_ate0_through_serial_queue(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        assert not window.echo_check.isChecked()
        assert not window.echo_check.isEnabled()
        window._on_connection_changed(True)
        assert window.echo_check.isEnabled()
        assert window.send_button.isEnabled()
        assert sent == []

        window.echo_check.setChecked(True)
        assert sent == [b"ATE1\r"]
        window._process_rx_line(b"ATE1\r\n")
        assert "RX ← ATE1" in window.console.toPlainText()
        assert sent == [b"ATE1\r"]
        window._process_rx_line(b"OK\r\n")

        window.echo_check.setChecked(False)
        assert sent == [b"ATE1\r", b"ATE0\r"]
    finally:
        window.close()


def test_connect_does_not_send_automatic_echo_command(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._on_connection_changed(True)
        assert sent == []
        assert not window.echo_check.isChecked()
        assert window.command_input.isEnabled()
        assert window.terminator_combo.isEnabled()
        assert window.status_label.text() == "Puerto conectado — modo pasivo"
        assert not window.initialize_at_check.isChecked()
        assert not window._initialization_timer.isActive()
        assert not window.automatic_monitor_check.isChecked()
        assert not window._automatic_monitor_timer.isActive()
    finally:
        window.close()


def test_passive_connect_allows_manual_sequential_commands(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window._on_connection_changed(True)
        window.command_input.setPlainText("AT\nATI")
        window._send_command()
        assert sent == [b"AT\r"]
        window._process_rx_line(b"OK\r\n")
        assert sent == [b"AT\r", b"ATI\r"]
        window._process_rx_line(b"OK\r\n")
        assert window._command_queue.pending_count == 0
    finally:
        window.close()


def test_opt_in_initialization_waits_then_sends_at_and_ate0_sequentially(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window.initialize_at_check.setChecked(True)
        window._on_connection_changed(True)
        assert window._initialization_timer.isActive()
        assert sent == []
        assert not window.command_input.isEnabled()

        window._initialization_timer.stop()
        window._start_at_initialization()
        assert sent == [b"AT\r"]
        window._on_transmitted(b"AT\r")
        assert "TX → AT" in window.console.toPlainText()
        window._process_rx_line(b"OK\r\n")
        assert sent == [b"AT\r", b"ATE0\r"]
        window._on_transmitted(b"ATE0\r")
        assert "TX → ATE0" in window.console.toPlainText()
        window._process_rx_line(b"OK\r\n")
        assert not window._initializing_at
        assert window.status_label.text() == "Puerto conectado — AT inicializado"
    finally:
        window.close()


def test_initialization_timeout_restores_manual_command_availability(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window.initialize_at_check.setChecked(True)
        window._on_connection_changed(True)
        window._initialization_timer.stop()
        window._start_at_initialization()
        assert sent == [b"AT\r"]

        window._on_command_timeout()

        assert not window._initializing_at
        assert not window._command_queue.paused
        assert window.send_button.isEnabled()
        assert "fallida" in window.status_label.text()
    finally:
        window.close()


def test_disconnect_stops_initialization_and_monitor_timers_and_reconnect_is_passive(
    qt_app, monkeypatch
):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        window.initialize_at_check.setChecked(True)
        window._on_connection_changed(True)
        assert window._initialization_timer.isActive()
        window._initialization_timer.stop()
        window._initializing_at = False

        window.automatic_monitor_check.setEnabled(True)
        window.automatic_monitor_check.setChecked(True)
        assert window._automatic_monitor_timer.isActive()
        window._on_connection_changed(False)
        assert not window._initialization_timer.isActive()
        assert not window._automatic_monitor_timer.isActive()
        assert not window.automatic_monitor_check.isChecked()
        assert window.monitor_status_label.text() == "Monitorización detenida"

        sent.clear()
        window.initialize_at_check.setChecked(False)
        window._on_connection_changed(True)
        assert sent == []
        assert not window.automatic_monitor_check.isChecked()
        assert not window._automatic_monitor_timer.isActive()
        assert not window._initialization_timer.isActive()
        assert window.status_label.text() == "Puerto conectado — modo pasivo"
    finally:
        window.close()
