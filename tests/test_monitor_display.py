from datetime import datetime
import json
import os

import pytest
from PySide6.QtWidgets import QApplication

import tdserial_iot.main_window as main_window_module
from tdserial_iot.app_config import DEFAULT_CONFIG
from tdserial_iot.command_queue import AtCommand, InteractionPlan
from tdserial_iot.main import configure_qt_plugin_paths
from tdserial_iot.main_window import MainWindow


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
        assert window.autoscroll_check.objectName() == "toolbarCheck"
        assert window.show_details_check.objectName() == "toolbarCheck"
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


def test_diagnostic_tab_uses_shared_queue_and_updates_sim_card(qt_app, monkeypatch):
    assert qt_app is QApplication.instance()
    monkeypatch.setattr(main_window_module, "load_config", lambda: DEFAULT_CONFIG.copy())
    monkeypatch.setattr(main_window_module, "save_config", lambda config: config.copy())
    window = MainWindow()
    sent: list[bytes] = []
    monkeypatch.setattr(window, "_write_queued_command", sent.append)
    try:
        assert window.tabs.tabText(1) == "Diagnóstico IoT"
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


def test_copy_diagnostic_includes_timestamp_all_fields_and_respects_identifier_mask(
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
        window._last_diagnostic_at = datetime(2026, 11, 9, 16, 10, 23)

        window.copy_diagnostic_button.click()
        report = qt_app.clipboard().text()
        assert "Fecha y hora: 09/11/2026 16:10:23" in report
        assert "Fabricante: SIMCOM" in report
        assert "Modelo: N/D" in report
        assert "IMEI: ***********2345" in report
        assert "ICCID: ***************4567" in report
        assert "123456789012345" not in report
        assert "Información ATI: SIM7080G" in report
        assert "Tecnología de acceso reportada: LTE NB-S1 (IoT)" in report
        assert "Calidad de señal NB-IoT: Buena (diagnóstico parcial; N/D: RSRQ, SINR)" in report

        window.reveal_identifiers_check.setChecked(True)
        window.copy_diagnostic_button.click()
        assert "IMEI: 123456789012345" in qt_app.clipboard().text()

        diagnostic_path = tmp_path / "diagnostic.json"
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: (str(diagnostic_path), "JSON (*.json)"),
        )
        window.reveal_identifiers_check.setChecked(False)
        window._export_diagnostic()
        exported = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        assert exported["diagnostic"]["Calidad de señal NB-IoT"].startswith("Buena")
        assert exported["diagnostic"]["IMEI"] == "***********2345"
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
