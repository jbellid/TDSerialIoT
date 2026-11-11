"""Main window for TdSerial IoT Phase 1."""

from __future__ import annotations

import json
import logging
import csv
import sqlite3
from email.utils import parsedate_to_datetime
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot, QDateTime
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports

from tdserial_iot.app_config import load_config, save_config
from tdserial_iot.command_queue import (
    AtCommand,
    AtCommandQueue,
    QueueEvent,
    make_at_command,
)
from tdserial_iot.controller_panel import ControllerPanel
from tdserial_iot.iot_operations import (
    UDP_MAX_PAYLOAD_BYTES,
    TransmissionRecord,
    build_cn_activate_command,
    build_cn_config_command,
    build_close_socket_command,
    build_open_socket_command,
    build_receive_command,
    build_send_command,
    encode_payload,
    parse_application_config,
    parse_application_context,
    parse_socket_state,
)
from tdserial_iot.onence_api import (
    ApiCredentials,
    CredentialStoreError,
    OneNCEAuthError,
    OneNCEAuthenticator,
    delete_api_credentials,
    load_api_credentials,
    store_api_credentials,
    delete_device_iccid,
    load_device_iccid,
    store_device_iccid,
)
from tdserial_iot.onence_historian import (
    HISTORIAN_MAX_PAGE_SIZE,
    HISTORIAN_RETENTION_DAYS,
    CloudMessage,
    HistoryPage,
    OneNCEHistorianClient,
    OneNCEHistoryError,
    OneNCEDevice,
    TransmissionCorrelation,
    correlate_transmissions,
    render_payload,
    sort_messages_newest_first,
)
from tdserial_iot.protocol import TERMINATORS, encode_at_commands
from tdserial_iot.serial_lines import CarecvFrameBuffer, SerialLineBuffer
from tdserial_iot.serial_worker import SerialWorker
from tdserial_iot.sim7080g import (
    FULL_DIAGNOSTIC_KEYS,
    MONITOR_KEYS,
    SIM7080G_COMMANDS,
    classify_signal_quality,
    parse_command_response,
    parse_registration,
    normalize_signal_technology,
    resolve_signal_quality_thresholds,
)

logger = logging.getLogger(__name__)

_IOT_QUERY_COMMANDS = {
    "registration": "AT+CEREG?",
    "attach": "AT+CGATT?",
    "pdp_state": "AT+CGACT?",
    "app_config": "AT+CNCFG?",
    "app_context": "AT+CNACT?",
    "ip": "AT+CGPADDR=1",
    "socket_state": "AT+CASTATE?",
    "verify_context": "AT+CNACT?",
    "verify_socket": "AT+CASTATE?",
}


class CommandTextEdit(QPlainTextEdit):
    """Multiline AT input; Enter sends and Shift+Enter inserts a newline."""

    send_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history: list[str] = []
        self.history_index = 0
        self._draft = ""

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_requested.emit()
            return
        cursor = self.textCursor()
        if (
            event.key() == Qt.Key.Key_Up
            and self.history
            and self.document().blockCount() == 1
            and cursor.atStart()
        ):
            if self.history_index == len(self.history):
                self._draft = self.toPlainText()
            self.history_index = max(0, self.history_index - 1)
            self.setPlainText(self.history[self.history_index])
            self.moveCursor(QTextCursor.MoveOperation.End)
            return
        if (
            event.key() == Qt.Key.Key_Down
            and self.history
            and self.document().blockCount() == 1
            and cursor.atEnd()
        ):
            self.history_index = min(len(self.history), self.history_index + 1)
            self.setPlainText(
                self.history[self.history_index]
                if self.history_index < len(self.history)
                else self._draft
            )
            self.moveCursor(QTextCursor.MoveOperation.End)
            return
        super().keyPressEvent(event)

    def remember(self, commands: Sequence[str]) -> None:
        for command in commands:
            if command and (not self.history or self.history[-1] != command):
                self.history.append(command)
        self.history_index = len(self.history)
        self._draft = ""


class OneNCEAuthTestWorker(QObject):
    completed = Signal(bool, str)

    def __init__(self, authenticator: OneNCEAuthenticator) -> None:
        super().__init__()
        self._authenticator = authenticator

    @Slot()
    def run(self) -> None:
        try:
            self._authenticator.get_access_token(force_refresh=True)
        except (OneNCEAuthError, ValueError) as exc:
            self.completed.emit(False, str(exc))
        else:
            self.completed.emit(True, "Autenticación correcta; token disponible.")


class OneNCEHistoryWorker(QObject):
    completed = Signal(bool, object, str)

    def __init__(
        self,
        authenticator: OneNCEAuthenticator,
        device_id: str | None,
        *,
        page_size: int,
        protocol: str,
        start_datetime: datetime | None,
        end_datetime: datetime | None,
        next_token: str | None,
        fetch_all: bool,
        max_records: int,
    ) -> None:
        super().__init__()
        self._authenticator = authenticator
        self._device_id = device_id
        self._page_size = page_size
        self._protocol = protocol
        self._start_datetime = start_datetime
        self._end_datetime = end_datetime
        self._next_token = next_token
        self._fetch_all = fetch_all
        self._max_records = max_records

    @Slot()
    def run(self) -> None:
        try:
            self._authenticator.get_access_token(force_refresh=True)
            client = OneNCEHistorianClient(self._authenticator)
            messages: list[CloudMessage] = []
            token = self._next_token
            seen_tokens: set[str] = {token} if token is not None else set()
            next_token: str | None = None
            while True:
                query = {
                    "page_size": self._page_size,
                    "protocol": self._protocol,
                    "start_datetime": self._start_datetime,
                    "end_datetime": self._end_datetime,
                    "next_token": token,
                }
                page = (
                    client.get_all_history_page(**query)
                    if self._device_id is None
                    else client.get_history_page(self._device_id, **query)
                )
                known_messages = {_cloud_message_key(item) for item in messages}
                for item in page.messages:
                    key = _cloud_message_key(item)
                    if key not in known_messages:
                        messages.append(item)
                        known_messages.add(key)
                next_token = page.next_token
                if not self._fetch_all or not next_token:
                    break
                if next_token in seen_tokens:
                    raise OneNCEHistoryError(
                        "Historian repitió un nextToken; se detuvo la paginación."
                    )
                seen_tokens.add(next_token)
                if len(messages) >= self._max_records:
                    messages = messages[: self._max_records]
                    break
                token = next_token
            self.completed.emit(
                True,
                HistoryPage(tuple(messages), next_token),
                "",
            )
        except (OneNCEAuthError, OneNCEHistoryError, ValueError) as exc:
            self.completed.emit(False, exc, str(exc))


class OneNCEDevicesWorker(QObject):
    completed = Signal(bool, object, str)

    def __init__(self, authenticator: OneNCEAuthenticator) -> None:
        super().__init__()
        self._authenticator = authenticator

    @Slot()
    def run(self) -> None:
        try:
            self._authenticator.get_access_token(force_refresh=True)
            client = OneNCEHistorianClient(self._authenticator)
            iccids = client.get_device_iccids()
            try:
                labels = client.get_sim_labels()
            except (OneNCEAuthError, OneNCEHistoryError, ValueError) as exc:
                devices = tuple(OneNCEDevice(iccid) for iccid in iccids)
                self.completed.emit(
                    True,
                    devices,
                    f"No se pudieron obtener los LABEL ({exc}); "
                    "se muestran los ICCID.",
                )
                return
            devices = tuple(
                OneNCEDevice(iccid, labels.get(iccid, ""))
                for iccid in iccids
            )
            self.completed.emit(True, devices, "")
        except (OneNCEAuthError, OneNCEHistoryError, ValueError) as exc:
            self.completed.emit(False, exc, str(exc))


def _cloud_message_key(message: CloudMessage) -> tuple[Any, ...]:
    if message.message_id:
        return (
            "message-id",
            message.device_id or message.iccid,
            message.message_id,
        )
    return (
        message.device_id,
        message.iccid,
        message.timestamp_original,
        message.protocol,
        message.payload_original,
    )


class MainWindow(QMainWindow):
    COMMAND_TIMEOUT_MS = 10_000
    AT_INITIALIZATION_DELAY_MS = 10_000

    open_requested = Signal(dict)
    write_requested = Signal(bytes)
    close_requested = Signal()
    stop_requested = Signal()
    _COLORS = {
        "TX": QColor("#3fb950"),
        "RX": QColor("#58a6ff"),
        "ERROR": QColor("#ff6b6b"),
        "SYSTEM": QColor("#d2a8ff"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TdSerial IoT")
        self.resize(1180, 780)
        self._config = load_config()
        self._signal_quality_thresholds = resolve_signal_quality_thresholds(
            self._config.get("signal_quality_thresholds")
        )
        self._is_connected = False
        self._connecting = False
        self._initializing_at = False
        self._paused = False
        self._pending_render: list[tuple[str, bytes, datetime]] = []
        self._records: list[tuple[str, bytes, datetime]] = []
        self._rx_line_buffer = SerialLineBuffer()
        self._carecv_frame_buffer = CarecvFrameBuffer()
        self._command_queue = AtCommandQueue()
        self._queue_status = "Lista"
        self._error_dialog: QMessageBox | None = None
        self._command_responses: dict[int, list[bytes]] = {}
        self._diagnostic_manual_remaining = 0
        self._diagnostic_manual_total = 0
        self._diagnostic_manual_completed = 0
        self._auto_monitor_remaining = 0
        self._auto_monitor_running = False
        self._manual_sequence_active = False
        self._diagnostic_manual_failed = False
        self._auto_monitor_failed = False
        self._diagnostic_values: dict[str, str] = {}
        self._diagnostic_field_titles: dict[str, str] = {}
        self._last_diagnostic_at: datetime | None = None
        self._iot_flow_action: str | None = None
        self._iot_flow_started_at: datetime | None = None
        self._iot_flow_responses: dict[str, list[bytes]] = {}
        self._iot_payload: bytes | None = None
        self._iot_payload_format = "JSON"
        self._iot_payload_tx_active = False
        self._iot_payload_tx_bytes = 0
        self._iot_received_payload = bytearray()
        self._iot_history: list[TransmissionRecord] = []
        self._iot_file_data: bytes | None = None
        self._iot_file_path: Path | None = None
        self._iot_operation_buttons: list[QPushButton] = []
        self._onence_auth_thread: QThread | None = None
        self._onence_auth_worker: OneNCEAuthTestWorker | None = None
        self._onence_authenticator: OneNCEAuthenticator | None = None
        self._onence_history_thread: QThread | None = None
        self._onence_history_worker: OneNCEHistoryWorker | None = None
        self._onence_devices_thread: QThread | None = None
        self._onence_devices_worker: OneNCEDevicesWorker | None = None
        self._onence_device_labels: dict[str, str] = {}
        self._cloud_messages: list[CloudMessage] = []
        self._cloud_payload_fields: list[str] = []
        self._cloud_correlation_by_message: dict[int, str] = {}
        self._cloud_next_token: str | None = None
        self._cloud_query_filters: dict[str, Any] | None = None
        self._cloud_query_started_at: datetime | None = None
        self._cloud_query_append = False
        self._cloud_history_complete = False
        self._cloud_auto_timer = QTimer(self)
        self._cloud_auto_timer.setSingleShot(True)
        self._cloud_auto_timer.timeout.connect(self._query_onence_history)
        self._automatic_monitor_timer = QTimer(self)
        self._automatic_monitor_timer.timeout.connect(self._run_automatic_monitor)
        self._initialization_timer = QTimer(self)
        self._initialization_timer.setSingleShot(True)
        self._initialization_timer.timeout.connect(self._start_at_initialization)
        self._rx_flush_timer = QTimer(self)
        self._rx_flush_timer.setSingleShot(True)
        self._rx_flush_timer.setInterval(100)
        self._rx_flush_timer.timeout.connect(self._flush_incomplete_rx_line)
        self._command_timeout_timer = QTimer(self)
        self._command_timeout_timer.setSingleShot(True)
        self._command_timeout_timer.setInterval(self.COMMAND_TIMEOUT_MS)
        self._command_timeout_timer.timeout.connect(self._on_command_timeout)

        self._thread = QThread(self)
        self._worker = SerialWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self.open_requested.connect(self._worker.open_port, Qt.ConnectionType.QueuedConnection)
        self.write_requested.connect(self._worker.write_bytes, Qt.ConnectionType.QueuedConnection)
        self.close_requested.connect(self._worker.close_port, Qt.ConnectionType.QueuedConnection)
        self.stop_requested.connect(self._worker.stop, Qt.ConnectionType.QueuedConnection)
        self._worker.received.connect(self._on_received)
        self._worker.transmitted.connect(self._on_transmitted)
        self._worker.connection_changed.connect(self._on_connection_changed)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.stopped.connect(
            self._thread.quit, Qt.ConnectionType.DirectConnection
        )
        self._thread.start()

        self._build_ui()
        self._apply_style()
        self._load_onence_credentials()
        self.command_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.terminator_combo.setEnabled(False)
        self.direct_send_check.setEnabled(False)
        self.echo_check.setEnabled(False)
        self._update_queue_controls()
        self.refresh_ports()

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        root.addWidget(self._build_connection_panel())
        self.tabs = QTabWidget()
        serial_page = QWidget()
        serial_layout = QVBoxLayout(serial_page)
        serial_layout.setContentsMargins(0, 8, 0, 0)
        serial_layout.addWidget(self._build_monitor_panel(), 1)
        serial_layout.addWidget(self._build_command_panel())
        self.tabs.addTab(serial_page, "Monitor serie")
        self.tabs.addTab(self._build_diagnostic_tab(), "Diagnóstico IoT")
        self.tabs.addTab(self._build_communications_tab(), "Comunicaciones IoT")
        self.tabs.addTab(self._build_onence_cloud_tab(), "1NCE Cloud")
        try:
            self.controller_panel: ControllerPanel | None = ControllerPanel(
                self._onence_credentials_from_fields
            )
        except (OSError, sqlite3.Error) as exc:
            logger.error("No se pudo abrir el almacenamiento de Device Controller: %s", exc)
            self.controller_panel = None
            self.tabs.addTab(
                QLabel("No se pudo abrir el almacenamiento local de Device Controller. Consulta el log."),
                "1NCE Device Controller",
            )
        else:
            controller_scroll = QScrollArea()
            controller_scroll.setWidgetResizable(True)
            controller_scroll.setWidget(self.controller_panel)
            self.controller_panel.refresh_devices_requested.connect(
                self._refresh_controller_cloud_devices
            )
            self.tabs.addTab(controller_scroll, "1NCE Device Controller")
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _build_onence_cloud_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        credentials_group = QGroupBox("Configuración API 1NCE")
        credentials_layout = QGridLayout(credentials_group)

        self.onence_client_id_edit = QLineEdit()
        self.onence_client_id_edit.setPlaceholderText("Client ID o usuario API")
        self.onence_client_secret_edit = QLineEdit()
        self.onence_client_secret_edit.setPlaceholderText(
            "Client Secret o contraseña API"
        )
        self.onence_client_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.onence_client_id_edit.textChanged.connect(
            self._invalidate_onence_authenticator
        )
        self.onence_client_secret_edit.textChanged.connect(
            self._invalidate_onence_authenticator
        )
        self.onence_auth_status_label = QLabel("Autenticación no probada")
        self.onence_auth_status_label.setObjectName("disconnectedStatus")
        self.onence_endpoint_label = QLabel(
            "OAuth: https://api.1nce.com/management-api/oauth/token · "
            "Historian: /v1/inspect/devices/{deviceId}/history"
        )
        self.onence_endpoint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.onence_test_button = QPushButton("Probar conexión")
        self.onence_test_button.setObjectName("primaryButton")
        self.onence_test_button.clicked.connect(self._test_onence_connection)
        self.onence_save_credentials_button = QPushButton("Guardar credenciales")
        self.onence_save_credentials_button.clicked.connect(
            self._save_onence_credentials
        )
        self.onence_delete_credentials_button = QPushButton("Borrar credenciales")
        self.onence_delete_credentials_button.clicked.connect(
            self._delete_onence_credentials
        )

        credentials_layout.addWidget(QLabel("Client ID / usuario API"), 0, 0)
        credentials_layout.addWidget(self.onence_client_id_edit, 0, 1, 1, 3)
        credentials_layout.addWidget(QLabel("Client Secret / contraseña API"), 1, 0)
        credentials_layout.addWidget(self.onence_client_secret_edit, 1, 1, 1, 3)
        credentials_layout.addWidget(self.onence_test_button, 2, 0)
        credentials_layout.addWidget(self.onence_save_credentials_button, 2, 1)
        credentials_layout.addWidget(self.onence_delete_credentials_button, 2, 2)
        credentials_layout.addWidget(self.onence_auth_status_label, 2, 3)
        credentials_layout.addWidget(self.onence_endpoint_label, 3, 0, 1, 4)
        layout.addWidget(credentials_group)
        layout.addWidget(
            QLabel(
                "Las credenciales se guardan únicamente en el almacén seguro de "
                "Windows. La autenticación no requiere que COM6 esté conectado."
            )
        )

        device_group = QGroupBox("Consulta de History")
        device_layout = QGridLayout(device_group)
        self.onence_device_mode_combo = self._combo(
            ["Todos los dispositivos", "Seleccionar dispositivo", "ICCID manual"],
            "Todos los dispositivos",
        )
        self.onence_device_mode_combo.currentTextChanged.connect(
            self._onence_device_mode_changed
        )
        # Load ICCID from secure credential manager instead of config
        stored_iccid = ""
        try:
            stored_iccid = load_device_iccid() or ""
        except CredentialStoreError as e:
            logger.warning("No se pudo cargar el ICCID del almacén seguro: %s", e)
        self.onence_iccid_edit = QLineEdit(stored_iccid)
        self.onence_iccid_edit.setPlaceholderText("Device ID exacto de Device Inspector")
        self.onence_iccid_edit.setToolTip(
            "Se envía exactamente el identificador escrito; no se quitan dígitos."
        )
        self.onence_iccid_edit.textChanged.connect(self._persist_iccid_to_secure_store)
        self.onence_device_combo = self._combo([], "")
        self.onence_device_combo.setToolTip("LABEL e ICCID del dispositivo 1NCE.")
        self.onence_device_combo.setEnabled(False)
        self.onence_device_input_stack = QStackedWidget()
        self.onence_device_input_stack.addWidget(QLabel("No se requiere ICCID"))
        self.onence_device_input_stack.addWidget(self.onence_device_combo)
        self.onence_device_input_stack.addWidget(self.onence_iccid_edit)
        self.onence_device_input_stack.setCurrentIndex(0)
        self.onence_update_devices_button = QPushButton("Actualizar dispositivos")
        self.onence_update_devices_button.clicked.connect(
            self._start_onence_devices_refresh
        )
        self.onence_get_iccid_button = QPushButton("Obtener ICCID del módem")
        self.onence_get_iccid_button.setToolTip(
            "Copia el ICCID AT+CCID completo. Puede diferir del Device ID de "
            "Device Inspector y no se recortan dígitos automáticamente."
        )
        self.onence_get_iccid_button.clicked.connect(self._get_onence_iccid_from_modem)
        self.onence_range_combo = self._combo(
            ["Últimos mensajes", "Última hora", "Últimas 24 horas", "Últimos 7 días", "Personalizado"],
            "Últimos 7 días",
        )
        self.onence_range_combo.currentTextChanged.connect(
            self._apply_onence_date_preset
        )
        self.onence_start_datetime = QDateTimeEdit()
        self.onence_start_datetime.setCalendarPopup(True)
        self.onence_start_datetime.setDisplayFormat("dd/MM/yyyy HH:mm:ss")
        self.onence_end_datetime = QDateTimeEdit()
        self.onence_end_datetime.setCalendarPopup(True)
        self.onence_end_datetime.setDisplayFormat("dd/MM/yyyy HH:mm:ss")
        self.onence_end_datetime.setDateTime(QDateTime.currentDateTime())
        self.onence_start_datetime.setDateTime(
            QDateTime.currentDateTime().addDays(-HISTORIAN_RETENTION_DAYS)
        )
        self.onence_protocol_combo = self._combo(
            ["UDP", "CoAP", "LwM2M", "Todos"], "UDP"
        )
        self.onence_page_size_spin = QSpinBox()
        self.onence_page_size_spin.setRange(1, HISTORIAN_MAX_PAGE_SIZE)
        self.onence_page_size_spin.setValue(10)
        self.onence_max_records_spin = QSpinBox()
        self.onence_max_records_spin.setRange(1, 1000)
        self.onence_max_records_spin.setValue(100)
        self.onence_query_button = QPushButton("Consultar History")
        self.onence_query_button.setObjectName("primaryButton")
        self.onence_query_button.clicked.connect(
            lambda: self._start_onence_history_query()
        )
        self.onence_more_button = QPushButton("Cargar más")
        self.onence_more_button.clicked.connect(
            lambda: self._start_onence_history_query(load_more=True)
        )
        self.onence_all_button = QPushButton("Recuperar todos (límite)")
        self.onence_all_button.clicked.connect(
            lambda: self._start_onence_history_query(fetch_all=True)
        )
        self.onence_auto_check = QCheckBox("Actualización automática")
        self.onence_auto_check.setObjectName("toolbarCheck")
        self.onence_auto_check.toggled.connect(self._toggle_onence_auto_update)
        self.onence_auto_interval_combo = self._combo(
            ["Manual", "30 segundos", "60 segundos", "5 minutos"], "Manual"
        )
        self.onence_auto_interval_combo.currentTextChanged.connect(
            self._update_onence_auto_interval
        )
        self.onence_history_status_label = QLabel("Historial no consultado")
        self.onence_last_query_label = QLabel("Última consulta: N/D")
        self.onence_stats_label = QLabel(
            "Registros en History: 0 · UDP: 0 · Envíos locales: 0"
        )
        device_layout.addWidget(QLabel("Modo de consulta"), 0, 0)
        device_layout.addWidget(self.onence_device_mode_combo, 0, 1)
        device_layout.addWidget(self.onence_device_input_stack, 0, 2, 1, 2)
        device_layout.addWidget(self.onence_update_devices_button, 0, 4)
        device_layout.addWidget(self.onence_get_iccid_button, 0, 5)
        device_id_help = QLabel(
            "Puedes consultar todos los dispositivos, seleccionar una SIM por LABEL "
            "o introducir manualmente el Device ID exacto de Device Inspector."
        )
        device_id_help.setWordWrap(True)
        device_layout.addWidget(device_id_help, 1, 0, 1, 6)
        device_layout.addWidget(QLabel("Periodo"), 2, 0)
        device_layout.addWidget(self.onence_range_combo, 2, 1)
        device_layout.addWidget(QLabel("Desde"), 2, 2)
        device_layout.addWidget(self.onence_start_datetime, 2, 3)
        device_layout.addWidget(QLabel("Hasta"), 2, 4)
        device_layout.addWidget(self.onence_end_datetime, 2, 5)
        device_layout.addWidget(QLabel("Protocolo"), 3, 0)
        device_layout.addWidget(self.onence_protocol_combo, 3, 1)
        device_layout.addWidget(QLabel("Por página"), 3, 2)
        device_layout.addWidget(self.onence_page_size_spin, 3, 3)
        device_layout.addWidget(QLabel("Máximo total"), 3, 4)
        device_layout.addWidget(self.onence_max_records_spin, 3, 5)
        device_layout.addWidget(self.onence_query_button, 4, 0)
        device_layout.addWidget(self.onence_more_button, 4, 1)
        device_layout.addWidget(self.onence_all_button, 4, 2)
        device_layout.addWidget(self.onence_auto_check, 4, 3)
        device_layout.addWidget(self.onence_auto_interval_combo, 4, 4)
        device_layout.addWidget(self.onence_history_status_label, 5, 0, 1, 3)
        device_layout.addWidget(self.onence_last_query_label, 5, 3, 1, 3)
        device_layout.addWidget(self.onence_stats_label, 6, 0, 1, 6)
        layout.addWidget(device_group)

        self.onence_history_table = QTableWidget(0, 8)
        self.onence_history_table.setHorizontalHeaderLabels(
            [
                "Fecha y hora",
                "LABEL",
                "ICCID",
                "Protocolo",
                "Contenido",
                "Tamaño",
                "Estado de decodificación",
                "Correlación",
            ]
        )
        self.onence_history_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.onence_history_table.setMinimumHeight(145)
        self.onence_history_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.onence_history_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.onence_history_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.onence_history_table.verticalHeader().setDefaultSectionSize(26)
        self.onence_history_table.itemSelectionChanged.connect(
            self._show_selected_onence_message
        )
        history_header = self.onence_history_table.horizontalHeader()
        history_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        history_header.setStretchLastSection(False)
        history_widths = (150, 170, 190, 85, 260, 70, 220, 190)
        for column, width in enumerate(history_widths):
            self.onence_history_table.setColumnWidth(column, width)
        layout.addWidget(self.onence_history_table, 1)

        detail_controls = QHBoxLayout()
        self.onence_payload_view_combo = self._combo(
            ["Original", "JSON", "Texto", "Base64 decodificado", "HEX"], "Original"
        )
        self.onence_payload_view_combo.currentTextChanged.connect(
            self._render_selected_onence_message
        )
        copy_payload_button = QPushButton("Copiar contenido")
        copy_payload_button.clicked.connect(self._copy_onence_message)
        copy_json_button = QPushButton("Copiar JSON")
        copy_json_button.clicked.connect(self._copy_onence_json)
        export_message_button = QPushButton("Exportar mensaje")
        export_message_button.clicked.connect(self._export_selected_onence_message)
        self.onence_export_json_button = QPushButton("Exportar JSON")
        self.onence_export_json_button.clicked.connect(
            lambda: self._export_onence_messages("json")
        )
        self.onence_export_csv_button = QPushButton("Exportar CSV")
        self.onence_export_csv_button.clicked.connect(
            lambda: self._export_onence_messages("csv")
        )
        detail_controls.addWidget(QLabel("Representación"))
        detail_controls.addWidget(self.onence_payload_view_combo)
        detail_controls.addWidget(copy_payload_button)
        detail_controls.addWidget(copy_json_button)
        detail_controls.addWidget(export_message_button)
        detail_controls.addWidget(self.onence_export_json_button)
        detail_controls.addWidget(self.onence_export_csv_button)
        layout.addLayout(detail_controls)
        self.onence_message_detail = QPlainTextEdit()
        self.onence_message_detail.setReadOnly(True)
        self.onence_message_detail.setMaximumHeight(150)
        layout.addWidget(self.onence_message_detail)
        return page

    def _load_onence_credentials(self) -> None:
        try:
            credentials = load_api_credentials()
        except CredentialStoreError as exc:
            self._set_onence_auth_status(str(exc), success=False)
            return
        if credentials is not None:
            self.onence_client_id_edit.setText(credentials.client_id)
            self.onence_client_secret_edit.setText(credentials.client_secret)
            self._onence_authenticator = OneNCEAuthenticator(credentials)
            self.onence_auth_status_label.setText(
                "Credenciales disponibles en el almacén seguro"
            )

    def _onence_credentials_from_fields(self) -> ApiCredentials:
        return ApiCredentials(
            client_id=self.onence_client_id_edit.text().strip(),
            client_secret=self.onence_client_secret_edit.text(),
        )

    def _invalidate_onence_authenticator(self) -> None:
        self._onence_authenticator = None

    def _test_onence_connection(self) -> None:
        if self._onence_auth_thread is not None:
            return
        try:
            credentials = self._onence_credentials_from_fields()
            if not credentials.client_id or not credentials.client_secret:
                raise ValueError("Introduce el identificador y el secreto API.")
        except ValueError as exc:
            self._set_onence_auth_status(str(exc), success=False)
            return

        authenticator = OneNCEAuthenticator(credentials)
        thread = QThread(self)
        worker = OneNCEAuthTestWorker(authenticator)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_onence_auth_test_completed)
        worker.completed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(
            lambda current=thread: self._on_onence_auth_thread_finished(current)
        )
        thread.finished.connect(thread.deleteLater)
        self._onence_authenticator = authenticator
        self._onence_auth_thread = thread
        self._onence_auth_worker = worker
        self.onence_test_button.setEnabled(False)
        self._set_onence_auth_status("Conectando con 1NCE…", success=None)
        thread.start()

    @Slot(bool, str)
    def _on_onence_auth_test_completed(self, success: bool, message: str) -> None:
        self.onence_test_button.setEnabled(True)
        self._set_onence_auth_status(message, success=success)
        if success:
            logger.info("Autenticación API 1NCE completada correctamente")
        else:
            logger.warning("Falló la autenticación API 1NCE: %s", message)

    def _on_onence_auth_thread_finished(self, thread: QThread) -> None:
        if self._onence_auth_thread is thread:
            self._onence_auth_thread = None
            self._onence_auth_worker = None

    def _save_onence_credentials(self) -> None:
        try:
            credentials = self._onence_credentials_from_fields()
            store_api_credentials(credentials)
        except (CredentialStoreError, ValueError) as exc:
            self._set_onence_auth_status(str(exc), success=False)
            return
        self._onence_authenticator = OneNCEAuthenticator(credentials)
        self._set_onence_auth_status(
            "Credenciales guardadas en el almacén seguro de Windows.", success=True
        )

    def _delete_onence_credentials(self) -> None:
        answer = QMessageBox.question(
            self,
            "Borrar credenciales 1NCE",
            "¿Quieres borrar las credenciales guardadas en el almacén seguro de Windows?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_api_credentials()
        except CredentialStoreError as exc:
            self._set_onence_auth_status(str(exc), success=False)
            return
        self.onence_client_id_edit.clear()
        self.onence_client_secret_edit.clear()
        self._onence_authenticator = None
        self._set_onence_auth_status("Credenciales borradas.", success=None)

    def _set_onence_auth_status(
        self, message: str, *, success: bool | None
    ) -> None:
        self.onence_auth_status_label.setText(message)
        object_name = (
            "connectedStatus"
            if success is True
            else "disconnectedStatus"
            if success is False
            else "disconnectedStatus"
        )
        self.onence_auth_status_label.setObjectName(object_name)
        self.onence_auth_status_label.style().unpolish(self.onence_auth_status_label)
        self.onence_auth_status_label.style().polish(self.onence_auth_status_label)

    def _get_onence_iccid_from_modem(self) -> None:
        self.onence_device_mode_combo.setCurrentText("ICCID manual")
        if not self._is_connected:
            self.onence_history_status_label.setText(
                "Puerto serie desconectado; introduce el ICCID manualmente."
            )
            return
        self._start_diagnostic(("iccid",))

    def _query_onence_history(self) -> None:
        if not self.onence_auto_check.isChecked():
            return
        self._start_onence_history_query()
        if self._onence_history_thread is None and not self._cloud_auto_timer.isActive():
            delay_ms = self._onence_auto_delay_ms()
            if delay_ms is not None:
                self._cloud_auto_timer.start(delay_ms)

    def _onence_device_mode_changed(self, mode: str) -> None:
        mode_index = {
            "Todos los dispositivos": 0,
            "Seleccionar dispositivo": 1,
            "ICCID manual": 2,
        }.get(mode, 0)
        self.onence_device_input_stack.setCurrentIndex(mode_index)
        if mode == "Seleccionar dispositivo" and self.onence_device_combo.count() == 0:
            self.onence_history_status_label.setText(
                "Actualiza los dispositivos para seleccionar una SIM por LABEL."
            )

    def _start_onence_devices_refresh(self) -> None:
        if self._onence_devices_thread is not None:
            self.onence_history_status_label.setText(
                "La actualización de dispositivos ya está en curso."
            )
            return
        if self._onence_authenticator is None:
            try:
                credentials = self._onence_credentials_from_fields()
                if not credentials.client_id or not credentials.client_secret:
                    raise ValueError(
                        "Configura las credenciales API antes de actualizar "
                        "los dispositivos."
                    )
            except ValueError as exc:
                self.onence_history_status_label.setText(str(exc))
                return
            self._onence_authenticator = OneNCEAuthenticator(credentials)

        thread = QThread(self)
        worker = OneNCEDevicesWorker(self._onence_authenticator)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_onence_devices_completed)
        worker.completed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(
            lambda current=thread: self._on_onence_devices_thread_finished(current)
        )
        thread.finished.connect(thread.deleteLater)
        self._onence_devices_thread = thread
        self._onence_devices_worker = worker
        self.onence_update_devices_button.setEnabled(False)
        self.onence_history_status_label.setText(
            "Actualizando dispositivos y LABEL desde 1NCE…"
        )
        thread.start()

    def _refresh_controller_cloud_devices(self) -> None:
        if self.controller_panel is None:
            return
        self._start_onence_devices_refresh()
        if self._onence_devices_thread is None:
            self.controller_panel._show_error(self.onence_history_status_label.text())
        else:
            self.controller_panel._log(self.onence_history_status_label.text())

    @Slot(bool, object, str)
    def _on_onence_devices_completed(
        self, success: bool, result: object, message: str
    ) -> None:
        self.onence_update_devices_button.setEnabled(True)
        if not success or not isinstance(result, tuple):
            self.onence_history_status_label.setText(message)
            logger.warning("No se pudieron actualizar los dispositivos 1NCE: %s", message)
            if self.controller_panel is not None:
                self.controller_panel._log(f"ERROR al actualizar dispositivos: {message}")
            return
        previous_iccid = self.onence_device_combo.currentData()
        self.onence_device_combo.clear()
        self._onence_device_labels.clear()
        seen_iccids: set[str] = set()
        for device in result:
            if not isinstance(device, OneNCEDevice) or device.iccid in seen_iccids:
                continue
            seen_iccids.add(device.iccid)
            self._onence_device_labels[device.iccid] = device.label
            label = device.label or device.iccid
            display = (
                f"{label} — {device.iccid}"
                if device.label and device.label != device.iccid
                else device.iccid
            )
            self.onence_device_combo.addItem(display, device.iccid)
        self.onence_device_combo.setEnabled(bool(seen_iccids))
        if previous_iccid is not None:
            previous_index = self.onence_device_combo.findData(previous_iccid)
            if previous_index >= 0:
                self.onence_device_combo.setCurrentIndex(previous_index)
        if message:
            logger.warning("Actualización parcial de dispositivos 1NCE: %s", message)
            status = message
        elif not seen_iccids:
            status = "No se encontraron dispositivos en la organización."
        else:
            status = f"Dispositivos actualizados: {len(seen_iccids)}."
        self.onence_history_status_label.setText(status)
        if self.controller_panel is not None:
            self.controller_panel.set_cloud_devices(list(self._onence_device_labels.items()))
            self.controller_panel._log(status)
        if self._cloud_messages:
            self._render_onence_history_table()

    def _on_onence_devices_thread_finished(self, thread: QThread) -> None:
        if self._onence_devices_thread is thread:
            self._onence_devices_thread = None
            self._onence_devices_worker = None

    def _apply_onence_date_preset(self, preset: str) -> None:
        if preset == "Personalizado":
            return
        now = QDateTime.currentDateTime()
        self.onence_end_datetime.setDateTime(now)
        if preset == "Última hora":
            start = now.addSecs(-60 * 60)
        elif preset == "Últimas 24 horas":
            start = now.addDays(-1)
        elif preset == "Últimos 7 días":
            start = now.addDays(-HISTORIAN_RETENTION_DAYS)
        else:
            self.onence_start_datetime.setDateTime(
                now.addDays(-HISTORIAN_RETENTION_DAYS)
            )
            return
        self.onence_start_datetime.setDateTime(start)

    @staticmethod
    def _local_datetime(edit: QDateTimeEdit) -> datetime:
        value = edit.dateTime().toPython()
        return value.astimezone() if value.tzinfo is None else value

    def _start_onence_history_query(
        self, *, load_more: bool = False, fetch_all: bool = False
    ) -> None:
        if self._onence_history_thread is not None:
            self.onence_history_status_label.setText(
                "Ya hay una consulta de History en curso."
            )
            return
        mode = self.onence_device_mode_combo.currentText()
        if mode == "Todos los dispositivos":
            device_id = None
        elif mode == "Seleccionar dispositivo":
            selected = self.onence_device_combo.currentData()
            device_id = str(selected).strip() if selected is not None else ""
        else:
            device_id = self.onence_iccid_edit.text().strip()
        if device_id == "":
            self.onence_history_status_label.setText(
                "Selecciona un dispositivo actualizado o introduce un "
                "ICCID / Device ID manual."
            )
            return
        if load_more and not self._cloud_next_token:
            self.onence_history_status_label.setText(
                "No hay más páginas disponibles; consulta de nuevo para actualizar."
            )
            return
        if load_more:
            if self._cloud_query_filters is None:
                self.onence_history_status_label.setText(
                    "No hay una consulta anterior para continuar."
                )
                return
            query_filters = self._cloud_query_filters
        else:
            start_datetime: datetime | None = None
            preset = self.onence_range_combo.currentText()
            if preset != "Personalizado":
                preset_end = QDateTime.currentDateTime()
                self.onence_end_datetime.setDateTime(preset_end)
                if preset == "Últimos 7 días":
                    self.onence_start_datetime.setDateTime(
                        preset_end.addDays(-HISTORIAN_RETENTION_DAYS).addSecs(60)
                    )
            end_datetime: datetime | None = None
            if preset != "Últimos mensajes":
                start_datetime = self._local_datetime(self.onence_start_datetime)
                end_datetime = self._local_datetime(self.onence_end_datetime)
                now = datetime.now().astimezone()
                if start_datetime > end_datetime:
                    self.onence_history_status_label.setText(
                        "La fecha inicial debe ser anterior a la fecha final."
                    )
                    return
                if end_datetime > now:
                    self.onence_history_status_label.setText(
                        "La fecha final no puede estar en el futuro."
                    )
                    return
                if start_datetime < now - timedelta(
                    days=HISTORIAN_RETENTION_DAYS, seconds=60
                ):
                    self.onence_history_status_label.setText(
                        "El intervalo supera la retención de siete días de Historian."
                    )
                    return
            query_filters = {
                "mode": mode,
                "device_id": device_id,
                "page_size": self.onence_page_size_spin.value(),
                "protocol": self.onence_protocol_combo.currentText(),
                "start_datetime": start_datetime,
                "end_datetime": end_datetime,
            }
            self._cloud_query_filters = query_filters
            self._cloud_history_complete = False

        if self._onence_authenticator is None:
            try:
                credentials = self._onence_credentials_from_fields()
                if not credentials.client_id or not credentials.client_secret:
                    raise ValueError(
                        "Configura las credenciales API antes de consultar History."
                    )
            except ValueError as exc:
                self.onence_history_status_label.setText(str(exc))
                return
            self._onence_authenticator = OneNCEAuthenticator(credentials)

        thread = QThread(self)
        worker = OneNCEHistoryWorker(
            self._onence_authenticator,
            query_filters["device_id"],
            page_size=query_filters["page_size"],
            protocol=query_filters["protocol"],
            start_datetime=query_filters["start_datetime"],
            end_datetime=query_filters["end_datetime"],
            next_token=self._cloud_next_token if load_more else None,
            fetch_all=fetch_all,
            max_records=self.onence_max_records_spin.value(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_onence_history_completed)
        worker.completed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(
            lambda current=thread: self._on_onence_history_thread_finished(current)
        )
        thread.finished.connect(thread.deleteLater)
        self._onence_history_thread = thread
        self._onence_history_worker = worker
        self._cloud_query_append = load_more
        self._cloud_query_started_at = datetime.now().astimezone()
        self.onence_last_query_label.setText(
            "Última consulta: "
            + self._cloud_query_started_at.strftime("%d/%m/%Y %H:%M:%S")
        )
        self.onence_query_button.setEnabled(False)
        self.onence_more_button.setEnabled(False)
        self.onence_all_button.setEnabled(False)
        self.onence_history_status_label.setText("Consultando 1NCE Historian…")
        thread.start()

    @Slot(bool, object, str)
    def _on_onence_history_completed(
        self,
        success: bool,
        page: object,
        message: str,
    ) -> None:
        append = self._cloud_query_append
        self._cloud_query_append = False
        self.onence_query_button.setEnabled(True)
        self.onence_more_button.setEnabled(True)
        self.onence_all_button.setEnabled(True)
        if not success or not isinstance(page, HistoryPage):
            self.onence_history_status_label.setText(message)
            logger.warning("Consulta de Historian fallida: %s", message)
            retry_after = (
                page.retry_after
                if isinstance(page, OneNCEHistoryError)
                else None
            )
            delay_ms = self._onence_auto_delay_ms(retry_after)
            if delay_ms is not None:
                self._cloud_auto_timer.start(delay_ms)
            return

        if not append:
            self._cloud_messages = []
            self.onence_history_table.setRowCount(0)
        existing_keys = {_cloud_message_key(item) for item in self._cloud_messages}
        for item in page.messages:
            key = _cloud_message_key(item)
            if key not in existing_keys:
                self._cloud_messages.append(item)
                existing_keys.add(key)
        self._cloud_messages = list(
            sort_messages_newest_first(self._cloud_messages)
        )
        self._cloud_next_token = page.next_token
        self._cloud_history_complete = page.next_token is None
        self._render_onence_history_table()
        self.onence_last_query_label.setText(
            "Última consulta: "
            + datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")
        )
        self.onence_history_status_label.setText(
            "Consulta correcta; no hay mensajes en el intervalo."
            if not self._cloud_messages
            else (
                f"History actualizado: {len(page.messages)} mensaje(s) "
                "en esta consulta."
            )
        )
        self._update_onence_statistics()
        delay_ms = self._onence_auto_delay_ms()
        if delay_ms is not None:
            self._cloud_auto_timer.start(delay_ms)

    def _onence_auto_delay_ms(self, retry_after: str | None = None) -> int | None:
        if not self.onence_auto_check.isChecked():
            return None
        interval_ms = {
            "30 segundos": 30_000,
            "60 segundos": 60_000,
            "5 minutos": 300_000,
        }.get(self.onence_auto_interval_combo.currentText())
        if interval_ms is None:
            return None
        if retry_after is None:
            return interval_ms
        try:
            retry_seconds = max(0, int(retry_after))
        except ValueError:
            try:
                retry_time = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, OverflowError):
                return interval_ms
            if retry_time.tzinfo is None:
                retry_time = retry_time.astimezone()
            retry_seconds = max(
                0,
                int((retry_time - datetime.now().astimezone()).total_seconds()),
            )
        return max(interval_ms, retry_seconds * 1000)

    def _on_onence_history_thread_finished(self, thread: QThread) -> None:
        if self._onence_history_thread is thread:
            self._onence_history_thread = None
            self._onence_history_worker = None

    def _append_onence_message_row(
        self, message: CloudMessage, message_index: int
    ) -> None:
        row = self.onence_history_table.rowCount()
        self.onence_history_table.insertRow(row)
        if message.timestamp is None:
            timestamp_text = message.timestamp_original or "N/D"
        elif message.has_timezone:
            timestamp_text = message.timestamp.astimezone().strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        else:
            timestamp_text = (
                message.timestamp.strftime("%d/%m/%Y %H:%M:%S")
                + " (zona no indicada)"
            )
        payload_bytes = (
            message.payload_bytes
            if message.payload_bytes is not None
            else message.payload_original.encode("utf-8", errors="replace")
        )
        decoded_payload = self._decoded_payload_for_export(message)
        payload_object = decoded_payload if isinstance(decoded_payload, dict) else {}
        content = self._format_onence_table_value(decoded_payload)
        if isinstance(decoded_payload, str):
            content = decoded_payload.replace("\n", " ").replace("\r", " ")
        iccid = message.iccid or message.device_id or "N/D"
        label = self._onence_device_labels.get(iccid, "") or iccid
        values = (
            timestamp_text,
            label,
            iccid,
            message.protocol,
            content,
            str(len(payload_bytes)),
            message.decode_status,
            self._cloud_message_correlation_status(message_index),
            *(
                self._format_onence_table_value(payload_object.get(field, ""))
                for field in self._cloud_payload_fields
            ),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setData(Qt.ItemDataRole.UserRole, message_index)
            self.onence_history_table.setItem(row, column, item)

    @staticmethod
    def _format_onence_table_value(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if value is None:
            return "null"
        return str(value)

    def _show_selected_onence_message(self) -> None:
        self._render_selected_onence_message()

    def _render_selected_onence_message(self, representation: str = "") -> None:
        row = self.onence_history_table.currentRow()
        if row < 0 or row >= len(self._cloud_messages):
            self.onence_message_detail.clear()
            return
        message = self._cloud_messages[row]
        selected_representation = (
            representation or self.onence_payload_view_combo.currentText()
        )
        try:
            payload_text = render_payload(message, selected_representation)
        except ValueError as exc:
            payload_text = f"No disponible: {exc}\n\nPayload original:\n{message.payload_original}"
        if message.timestamp is None:
            timestamp_text = message.timestamp_original or "N/D"
        elif message.has_timezone:
            timestamp_text = message.timestamp.astimezone().isoformat()
        else:
            timestamp_text = message.timestamp.isoformat() + " (zona no indicada)"
        self.onence_message_detail.setPlainText(
            f"Fecha: {timestamp_text}\n"
            f"ICCID: {message.iccid}\n"
            f"Protocolo: {message.protocol}\n"
            f"Tipo: {message.content_type} · {message.decode_status}\n\n"
            f"Payload original:\n{message.payload_original}\n\n"
            f"Representación {selected_representation}:\n"
            f"{payload_text}"
        )

    def _copy_onence_message(self) -> None:
        row = self.onence_history_table.currentRow()
        if row < 0 or row >= len(self._cloud_messages):
            self.onence_history_status_label.setText(
                "Selecciona un mensaje para copiar su contenido."
            )
            return
        message = self._cloud_messages[row]
        try:
            text = render_payload(
                message, self.onence_payload_view_combo.currentText()
            )
        except ValueError as exc:
            self.onence_history_status_label.setText(str(exc))
            return
        QApplication.clipboard().setText(text)

    def _copy_onence_json(self) -> None:
        row = self.onence_history_table.currentRow()
        if row < 0 or row >= len(self._cloud_messages):
            self.onence_history_status_label.setText(
                "Selecciona un mensaje para copiar su JSON."
            )
            return
        message = self._cloud_messages[row]
        try:
            text = render_payload(message, "JSON")
        except ValueError as exc:
            self.onence_history_status_label.setText(str(exc))
            return
        QApplication.clipboard().setText(text)

    @staticmethod
    def _decoded_payload_for_export(message: CloudMessage) -> Any:
        try:
            return json.loads(render_payload(message, "JSON"))
        except ValueError:
            pass
        if message.payload_bytes is None:
            return None
        try:
            return message.payload_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return {"hex": message.payload_bytes.hex(" ").upper()}

    def _export_selected_onence_message(self) -> None:
        row = self.onence_history_table.currentRow()
        if row < 0 or row >= len(self._cloud_messages):
            self.onence_history_status_label.setText(
                "Selecciona un mensaje para exportarlo."
            )
            return
        message = self._cloud_messages[row]
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar mensaje 1NCE",
            str(Path.home() / "onence-message.json"),
            "Archivo JSON (*.json)",
        )
        if not filename:
            return
        content = {
            "time": message.timestamp_original,
            "label": self._onence_device_labels.get(
                message.iccid or message.device_id, ""
            ),
            "iccid": message.iccid,
            "deviceId": message.device_id,
            "protocol": message.protocol,
            "payload": message.payload_original,
            "payload_decoded": self._decoded_payload_for_export(message),
            "content_type": message.content_type,
            "decode_status": message.decode_status,
            "correlation_status": self._cloud_message_correlation_status(row),
        }
        try:
            Path(filename).write_text(
                json.dumps(content, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.exception("No se pudo exportar el mensaje 1NCE a %s", filename)
            QMessageBox.critical(
                self, "Error al exportar", f"No se pudo guardar el mensaje.\n{exc}"
            )

    def _update_onence_statistics(self) -> None:
        udp_count = sum(message.protocol == "UDP" for message in self._cloud_messages)
        correlations = self._get_onence_correlations()
        self._cloud_correlation_by_message = {
            item.cloud_index: item.status
            for item in correlations
            if item.cloud_index is not None
        }
        local_sends = len(correlations)
        confirmed = sum(
            item.status == "Confirmado en 1NCE" for item in correlations
        )
        pending = sum(
            item.status == "Pendiente de confirmación en 1NCE"
            for item in correlations
        )
        not_found = sum(
            item.status == "No encontrado en el periodo consultado"
            for item in correlations
        )
        aware_timestamps = [
            message.timestamp
            for message in self._cloud_messages
            if message.timestamp is not None and message.timestamp.tzinfo is not None
        ]
        naive_timestamps = [
            message.timestamp
            for message in self._cloud_messages
            if message.timestamp is not None and message.timestamp.tzinfo is None
        ]
        if aware_timestamps and not naive_timestamps:
            latest = max(aware_timestamps).astimezone()
            latest_text = latest.strftime("%d/%m/%Y %H:%M:%S")
        elif naive_timestamps and not aware_timestamps:
            latest = max(naive_timestamps)
            latest_text = latest.strftime("%d/%m/%Y %H:%M:%S (zona no indicada)")
        elif aware_timestamps and naive_timestamps:
            latest_text = "N/D (zonas horarias mixtas)"
        else:
            latest_text = "N/D"
        self.onence_stats_label.setText(
            f"Registros recibidos en esta consulta: {len(self._cloud_messages)} · "
            f"UDP: {udp_count} · Envíos locales: {local_sends} · "
            f"Pendientes: {pending} · Confirmados: {confirmed} · "
            f"No encontrados en la consulta: {not_found} · "
            f"Último mensaje: {latest_text} · "
            "'No encontrado' no confirma una pérdida"
        )

    def _get_onence_correlations(self) -> tuple[TransmissionCorrelation, ...]:
        if self._cloud_query_filters is not None:
            if self._cloud_query_filters.get("mode") == "Todos los dispositivos":
                return ()
            device_id = self._cloud_query_filters.get("device_id")
        else:
            device_id = self.onence_iccid_edit.text().strip()
        if not device_id:
            return ()
        return correlate_transmissions(
            self._iot_history,
            self._cloud_messages,
            device_id,
            history_queried=self._cloud_history_complete,
        )

    def _cloud_message_correlation_status(self, cloud_index: int) -> str:
        return self._cloud_correlation_by_message.get(cloud_index, "—")

    def _render_onence_history_table(self) -> None:
        correlations = self._get_onence_correlations()
        self._cloud_correlation_by_message = {
            item.cloud_index: item.status
            for item in correlations
            if item.cloud_index is not None
        }
        decoded_payloads = [
            self._decoded_payload_for_export(message)
            for message in self._cloud_messages
        ]
        self._cloud_payload_fields = list(
            dict.fromkeys(
                field
                for payload in decoded_payloads
                if isinstance(payload, dict)
                for field in payload
            )
        )
        headers = [
            "Fecha y hora",
            "LABEL",
            "ICCID",
            "Protocolo",
            "Contenido",
            "Tamaño",
            "Estado de decodificación",
            "Correlación",
            *self._cloud_payload_fields,
        ]
        self.onence_history_table.setColumnCount(len(headers))
        self.onence_history_table.setHorizontalHeaderLabels(headers)
        for column, field in enumerate(self._cloud_payload_fields, start=8):
            width = max(
                100,
                min(
                    180,
                    self.onence_history_table.fontMetrics().horizontalAdvance(field)
                    + 28,
                ),
            )
            self.onence_history_table.setColumnWidth(column, width)
        self.onence_history_table.setRowCount(0)
        for index, message_item in enumerate(self._cloud_messages):
            self._append_onence_message_row(message_item, index)

    def _toggle_onence_auto_update(self, enabled: bool) -> None:
        self._cloud_auto_timer.stop()
        if not enabled:
            return
        self._update_onence_auto_interval(self.onence_auto_interval_combo.currentText())

    def _update_onence_auto_interval(self, interval: str) -> None:
        self._cloud_auto_timer.stop()
        if not self.onence_auto_check.isChecked():
            return
        interval_ms = {
            "30 segundos": 30_000,
            "60 segundos": 60_000,
            "5 minutos": 300_000,
        }.get(interval)
        if interval_ms is None:
            self.onence_history_status_label.setText(
                "Selecciona un intervalo para activar la actualización automática."
            )
            self.onence_auto_check.blockSignals(True)
            self.onence_auto_check.setChecked(False)
            self.onence_auto_check.blockSignals(False)
            return
        self._cloud_auto_timer.start(interval_ms)

    def _export_onence_messages(self, file_format: str) -> None:
        if not self._cloud_messages:
            self.onence_history_status_label.setText("No hay mensajes para exportar.")
            return
        extension = "json" if file_format == "json" else "csv"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar mensajes 1NCE",
            str(Path.home() / f"onence-history.{extension}"),
            f"Archivos {extension.upper()} (*.{extension})",
        )
        if not filename:
            return
        rows = [
            {
                "time": message.timestamp_original,
                "label": self._onence_device_labels.get(
                    message.iccid or message.device_id, ""
                ),
                "iccid": message.iccid,
                "deviceId": message.device_id,
                "protocol": message.protocol,
                "payload": message.payload_original,
                "payload_decoded": self._decoded_payload_for_export(message),
                "content_type": message.content_type,
                "decode_status": message.decode_status,
                "correlation_status": self._cloud_message_correlation_status(index),
            }
            for index, message in enumerate(self._cloud_messages)
        ]
        try:
            if extension == "json":
                Path(filename).write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            else:
                with Path(filename).open("w", encoding="utf-8-sig", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=rows[0].keys())
                    writer.writeheader()
                    csv_rows = [
                        {
                            **row,
                            "payload_decoded": json.dumps(
                                row["payload_decoded"], ensure_ascii=False
                            ),
                        }
                        for row in rows
                    ]
                    writer.writerows(csv_rows)
        except OSError as exc:
            logger.exception("No se pudo exportar Historian a %s", filename)
            QMessageBox.critical(
                self, "Error al exportar", f"No se pudo guardar el archivo.\n{exc}"
            )

    def _build_diagnostic_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.run_full_diagnostic_button = QPushButton("Ejecutar diagnóstico completo")
        self.run_full_diagnostic_button.setObjectName("primaryButton")
        self.run_full_diagnostic_button.clicked.connect(
            lambda: self._start_diagnostic(FULL_DIAGNOSTIC_KEYS)
        )
        self.copy_diagnostic_button = QPushButton("Copiar diagnóstico")
        self.copy_diagnostic_button.setToolTip(
            "Copia la fecha y todos los parámetros del panel de diagnóstico."
        )
        self.copy_diagnostic_button.clicked.connect(self._copy_diagnostic)
        self.export_diagnostic_button = QPushButton("Exportar diagnóstico")
        self.export_diagnostic_button.clicked.connect(self._export_diagnostic)
        self.diagnostic_progress_label = QLabel("Diagnóstico no ejecutado")
        self.last_diagnostic_label = QLabel("Último diagnóstico: N/D")
        self.automatic_monitor_check = QCheckBox("Monitorización automática")
        self.automatic_monitor_check.setObjectName("toolbarCheck")
        self.automatic_monitor_check.toggled.connect(self._toggle_automatic_monitor)
        self.monitor_interval_combo = self._combo(
            ["10 segundos", "30 segundos", "60 segundos", "5 minutos"],
            str(self._config.get("monitor_interval", "30 segundos")),
        )
        self.monitor_interval_combo.currentTextChanged.connect(
            self._update_automatic_monitor_interval
        )
        self.stop_monitor_button = QPushButton("Detener monitorización")
        self.stop_monitor_button.clicked.connect(
            lambda: self.automatic_monitor_check.setChecked(False)
        )
        self.monitor_status_label = QLabel("Monitorización detenida")
        self.monitor_status_label.setObjectName("disconnectedStatus")
        controls.addWidget(self.run_full_diagnostic_button)
        controls.addWidget(self.copy_diagnostic_button)
        controls.addWidget(self.export_diagnostic_button)
        controls.addWidget(self.diagnostic_progress_label, 1)
        controls.addWidget(self.last_diagnostic_label)
        layout.addLayout(controls)
        monitor_controls = QHBoxLayout()
        monitor_controls.addWidget(self.automatic_monitor_check)
        monitor_controls.addWidget(self.monitor_interval_combo)
        monitor_controls.addWidget(self.stop_monitor_button)
        monitor_controls.addWidget(self.monitor_status_label)
        monitor_controls.addStretch(1)
        layout.addLayout(monitor_controls)
        layout.addWidget(
            QLabel(
                "Los estados reflejan la información reportada por el módem; "
                "no verifican conectividad extremo a extremo."
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 4, 8, 8)
        grid.setSpacing(10)
        self._diagnostic_fields: dict[str, QLabel] = {}
        self._diagnostic_title_labels: dict[str, QLabel] = {}
        self._diagnostic_field_titles = {}
        cards = [
            (
                "Identificación",
                ("Fabricante", "manufacturer"),
                ("Modelo", "model"),
                ("Firmware", "firmware"),
                ("IMEI", "imei"),
                ("ICCID", "iccid"),
                ("IMSI", "imsi"),
            ),
            ("Estado de la SIM", ("Estado", "sim")),
            (
                "Red móvil",
                ("Registro", "registration"),
                ("Operador", "operator"),
                ("MCC/MNC", "operator_code"),
                ("Roaming", "roaming"),
                ("Tecnología", "technology"),
                ("Banda", "band"),
                ("Celda", "cell"),
            ),
            (
                "Calidad de señal",
                ("CSQ", "csq"),
                ("Calidad de señal", "signal_quality"),
                ("RSRP medido", "rsrp"),
                ("Clasificación RSRP", "rsrp_quality"),
                ("RSRQ medido", "rsrq"),
                ("Clasificación RSRQ", "rsrq_quality"),
                ("RSSNR reportado", "rssnr"),
                ("SINR calculado desde RSSNR", "sinr"),
                ("Clasificación SINR", "sinr_quality"),
                ("RSSI medido", "rssi_measured"),
                ("RSSI estimado desde CSQ", "rssi"),
                ("Indicador complementario CSQ", "coverage"),
            ),
            (
                "Conectividad de datos",
                ("Servicio de paquetes", "attach"),
                ("Contextos PDP configurados", "pdp"),
                ("Estado de contextos PDP", "pdp_state"),
                ("APN", "apn"),
                ("Dirección IP", "ip"),
                ("Contextos de aplicación", "app_context"),
            ),
            ("Sockets", ("Estado", "sockets")),
        ]
        for index, card in enumerate(cards):
            title, *rows = card
            row, column = divmod(index, 2)
            grid.addWidget(
                self._build_diagnostic_card(title, rows),
                row,
                column,
            )
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def _build_diagnostic_card(
        self, title: str, rows: list[tuple[str, str]]
    ) -> QGroupBox:
        card = QGroupBox(title)
        layout = QGridLayout(card)
        for row, (label, key) in enumerate(rows):
            value = QLabel("N/D")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._diagnostic_fields[key] = value
            self._diagnostic_field_titles[key] = label
            title_label = QLabel(label)
            self._diagnostic_title_labels[key] = title_label
            layout.addWidget(title_label, row, 0)
            layout.addWidget(value, row, 1)
        action_row = len(rows)
        button_text = {
            "Identificación": "Actualizar identificación",
            "Estado de la SIM": "Consultar SIM",
            "Red móvil": "Actualizar red",
            "Calidad de señal": "Actualizar señal",
            "Conectividad de datos": "Actualizar datos",
            "Sockets": "Consultar sockets",
        }[title]
        button = QPushButton(button_text)
        keys_by_card = {
            "Identificación": FULL_DIAGNOSTIC_KEYS[:7],
            "Estado de la SIM": ("sim",),
            "Red móvil": ("registration", "operator", "cpsi"),
            "Calidad de señal": ("csq", "cpsi"),
            "Conectividad de datos": ("attach", "pdp", "contexts", "ip", "app_context"),
            "Sockets": ("sockets",),
        }
        button.clicked.connect(
            lambda _checked=False, keys=keys_by_card[title]: self._start_diagnostic(keys)
        )
        layout.addWidget(button, action_row, 0, 1, 2)
        return card

    def _build_communications_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        config_group = QGroupBox("Conexión UDP")
        config = QGridLayout(config_group)

        self.iot_host_edit = QLineEdit(str(self._config.get("iot_host", "udp.os.1nce.com")))
        self.iot_port_spin = self._iot_spin("iot_port", 4445, 1, 65535)
        self.iot_context_spin = self._iot_spin("iot_context_id", 0, 0, 3)
        self.iot_socket_spin = self._iot_spin("iot_socket_id", 0, 0, 12)
        self.iot_apn_edit = QLineEdit(str(self._config.get("iot_apn", "iot.1nce.net")))
        self.iot_read_length_spin = self._iot_spin(
            "iot_read_length", UDP_MAX_PAYLOAD_BYTES, 1, UDP_MAX_PAYLOAD_BYTES
        )
        self.iot_prompt_timeout_spin = self._iot_spin(
            "iot_prompt_timeout_ms", 10_000, 100, 60_000
        )
        self.iot_prompt_timeout_spin.setSuffix(" ms")
        self.iot_input_time_spin = self._iot_spin(
            "iot_input_time_ms", 5000, 100, 10_000
        )
        self.iot_input_time_spin.setSuffix(" ms")
        self.iot_response_timeout_spin = self._iot_spin(
            "iot_response_timeout_ms", 30_000, 1_000, 600_000
        )
        self.iot_response_timeout_spin.setSuffix(" ms")

        profile_button = QPushButton("Aplicar perfil 1NCE OS — UDP")
        profile_button.clicked.connect(self._apply_1nce_profile)
        config.addWidget(QLabel("Protocolo"), 0, 0)
        config.addWidget(QLabel("UDP"), 0, 1)
        config.addWidget(QLabel("Host remoto"), 0, 2)
        config.addWidget(self.iot_host_edit, 0, 3)
        config.addWidget(QLabel("Puerto remoto"), 0, 4)
        config.addWidget(self.iot_port_spin, 0, 5)
        config.addWidget(QLabel("APN"), 1, 0)
        config.addWidget(self.iot_apn_edit, 1, 1)
        config.addWidget(QLabel("Contexto de aplicaciones"), 1, 2)
        config.addWidget(self.iot_context_spin, 1, 3)
        config.addWidget(QLabel("Socket"), 1, 4)
        config.addWidget(self.iot_socket_spin, 1, 5)
        config.addWidget(QLabel("Timeout de prompt"), 2, 0)
        config.addWidget(self.iot_prompt_timeout_spin, 2, 1)
        config.addWidget(QLabel("Ventana de entrada CASEND"), 2, 2)
        config.addWidget(self.iot_input_time_spin, 2, 3)
        config.addWidget(QLabel("Timeout de respuesta"), 2, 4)
        config.addWidget(self.iot_response_timeout_spin, 2, 5)
        config.addWidget(profile_button, 0, 6, 2, 1)

        state_group = QGroupBox("Estado de conexión")
        state = QGridLayout(state_group)
        self.iot_state_labels: dict[str, QLabel] = {}
        state_fields = (
            ("Registro móvil", "registration"),
            ("Servicio de paquetes", "attach"),
            ("Contextos PDP", "pdp_state"),
            ("Contexto de aplicaciones", "app_context"),
            ("APN configurado", "apn"),
            ("Dirección IP", "ip"),
            ("Socket", "socket"),
            ("Destino UDP", "destination"),
            ("Última operación", "last_operation"),
        )
        for index, (title, key) in enumerate(state_fields):
            row, column = divmod(index, 3)
            state.addWidget(QLabel(title), row, column * 2)
            value = QLabel("N/D")
            value.setWordWrap(True)
            self.iot_state_labels[key] = value
            state.addWidget(value, row, column * 2 + 1)

        operation_buttons = QHBoxLayout()
        for title, action in (
            ("Consultar estado", "state"),
            ("Activar contexto", "activate"),
            ("Abrir socket", "open"),
            ("Cerrar socket", "close"),
            ("Consultar datos recibidos", "receive"),
        ):
            button = QPushButton(title)
            button.clicked.connect(
                lambda _checked=False, operation=action: self._start_iot_flow(operation)
            )
            operation_buttons.addWidget(button)
            self._iot_operation_buttons.append(button)
            setattr(self, f"iot_{action}_button", button)

        message_group = QGroupBox("Editor de mensajes")
        message_layout = QVBoxLayout(message_group)
        message_controls = QHBoxLayout()
        format_value = str(self._config.get("iot_payload_format", "JSON"))
        self.iot_format_combo = self._combo(
            ["ASCII / UTF-8", "JSON", "HEX", "Binario de archivo"],
            format_value,
        )
        self.iot_format_combo.currentTextChanged.connect(
            self._update_iot_payload_preview
        )
        self.iot_format_combo.currentTextChanged.connect(
            self._toggle_iot_payload_controls
        )
        self.iot_choose_file_button = QPushButton("Seleccionar archivo")
        self.iot_choose_file_button.clicked.connect(self._choose_iot_file)
        self.iot_format_json_button = QPushButton("Formatear JSON")
        self.iot_format_json_button.clicked.connect(self._format_iot_json)
        message_controls.addWidget(QLabel("Formato"))
        message_controls.addWidget(self.iot_format_combo)
        message_controls.addWidget(self.iot_choose_file_button)
        message_controls.addWidget(self.iot_format_json_button)
        self.iot_file_label = QLabel("Sin archivo seleccionado")
        message_controls.addWidget(self.iot_file_label, 1)
        message_layout.addLayout(message_controls)
        self.iot_payload_edit = QPlainTextEdit()
        self.iot_payload_edit.setPlaceholderText(
            'Ejemplo JSON: {"temp":24.5,"hum":62,"bat":3.85}'
        )
        if format_value == "JSON":
            self.iot_payload_edit.setPlainText(
                '{"temp":24.5,"hum":62,"bat":3.85}'
            )
        self.iot_payload_edit.textChanged.connect(self._update_iot_payload_preview)
        message_layout.addWidget(self.iot_payload_edit, 1)
        preview_row = QHBoxLayout()
        self.iot_payload_status_label = QLabel("Introduce el mensaje que quieres enviar.")
        self.iot_payload_size_label = QLabel("0 bytes")
        self.iot_read_length_spin.setToolTip(
            f"Tamaño máximo documentado por SIM7080G: {UDP_MAX_PAYLOAD_BYTES} bytes."
        )
        preview_row.addWidget(self.iot_payload_status_label, 1)
        preview_row.addWidget(QLabel("Longitud:"))
        preview_row.addWidget(self.iot_payload_size_label)
        preview_row.addWidget(QLabel("Máx. recepción:"))
        preview_row.addWidget(self.iot_read_length_spin)
        self.iot_send_button = QPushButton("Enviar datos")
        self.iot_send_button.setObjectName("primaryButton")
        self.iot_send_button.clicked.connect(lambda: self._start_iot_flow("send"))
        preview_row.addWidget(self.iot_send_button)
        message_layout.addLayout(preview_row)

        self.iot_bridge_warning = QLabel(
            "Aviso: COM6 pasa por el puente serie del ESP32-S3. La aplicación registra "
            "los bytes escritos al puerto, pero no puede garantizar que el firmware "
            "los reenvíe sin cambios. Un OK del módem no confirma recepción remota UDP."
        )
        self.iot_bridge_warning.setWordWrap(True)

        result_group = QGroupBox("Resultados e historial")
        result_layout = QVBoxLayout(result_group)
        self.iot_results = QPlainTextEdit()
        self.iot_results.setReadOnly(True)
        self.iot_results.setMaximumBlockCount(1000)
        result_layout.addWidget(self.iot_results, 1)
        history_buttons = QHBoxLayout()
        self.iot_copy_history_button = QPushButton("Copiar historial")
        self.iot_copy_history_button.clicked.connect(self._copy_iot_history)
        self.iot_export_history_button = QPushButton("Exportar historial")
        self.iot_export_history_button.clicked.connect(self._export_iot_history)
        history_buttons.addStretch(1)
        history_buttons.addWidget(self.iot_copy_history_button)
        history_buttons.addWidget(self.iot_export_history_button)
        result_layout.addLayout(history_buttons)

        layout.addWidget(config_group)
        layout.addWidget(state_group)
        layout.addLayout(operation_buttons)
        layout.addWidget(message_group, 1)
        layout.addWidget(self.iot_bridge_warning)
        layout.addWidget(result_group, 1)
        self._toggle_iot_payload_controls()
        self._update_iot_payload_preview()
        return page

    def _iot_spin(self, key: str, default: int, minimum: int, maximum: int) -> QSpinBox:
        try:
            value = int(self._config.get(key, default))
        except (TypeError, ValueError):
            value = default
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(min(maximum, max(minimum, value)))
        return spin

    def _apply_1nce_profile(self) -> None:
        self.iot_host_edit.setText("udp.os.1nce.com")
        self.iot_port_spin.setValue(4445)
        self.iot_apn_edit.setText("iot.1nce.net")
        self.iot_context_spin.setValue(0)
        self.iot_socket_spin.setValue(0)
        self.iot_format_combo.setCurrentText("JSON")
        self._append_iot_result("Perfil 1NCE OS — UDP cargado; revisa los parámetros antes de operar.")

    def _toggle_iot_payload_controls(self, _format: str = "") -> None:
        is_json = self.iot_format_combo.currentText() == "JSON"
        is_file = self.iot_format_combo.currentText() == "Binario de archivo"
        self.iot_format_json_button.setEnabled(is_json)
        self.iot_choose_file_button.setEnabled(is_file)
        self.iot_payload_edit.setEnabled(not is_file)

    def _update_iot_payload_preview(self, _format: str = "") -> None:
        selected_format = self.iot_format_combo.currentText()
        try:
            payload = encode_payload(
                selected_format,
                self.iot_payload_edit.toPlainText(),
                self._iot_file_data,
            )
        except (UnicodeEncodeError, ValueError) as exc:
            self.iot_payload_size_label.setText("N/D")
            self.iot_payload_status_label.setText(str(exc))
            self.iot_send_button.setEnabled(False)
            return
        self.iot_payload_size_label.setText(f"{len(payload)} bytes")
        if len(payload) > UDP_MAX_PAYLOAD_BYTES:
            self.iot_payload_status_label.setText(
                f"El payload supera el límite documentado de {UDP_MAX_PAYLOAD_BYTES} bytes."
            )
            self.iot_send_button.setEnabled(False)
            return
        if selected_format == "JSON":
            self.iot_payload_status_label.setText("JSON válido; se enviará sin reformatearlo.")
        else:
            self.iot_payload_status_label.setText("Payload válido; no se añadirán CR ni LF.")
        self.iot_send_button.setEnabled(bool(payload) and self._iot_flow_action is None)

    def _choose_iot_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar payload binario", str(Path.home()), "Todos los archivos (*)"
        )
        if not filename:
            return
        path = Path(filename)
        try:
            file_size = path.stat().st_size
            if file_size > UDP_MAX_PAYLOAD_BYTES:
                self._append_iot_result(
                    f"Archivo rechazado: {file_size} bytes excede el máximo documentado "
                    f"de {UDP_MAX_PAYLOAD_BYTES} bytes."
                )
                return
            with path.open("rb") as source:
                data = source.read(UDP_MAX_PAYLOAD_BYTES + 1)
            if len(data) > UDP_MAX_PAYLOAD_BYTES:
                self._append_iot_result(
                    f"Archivo rechazado: supera {UDP_MAX_PAYLOAD_BYTES} bytes."
                )
                return
        except OSError as exc:
            logger.exception("No se pudo leer el archivo binario %s", path)
            QMessageBox.critical(
                self, "Error de archivo", f"No se pudo leer el archivo.\n{exc}"
            )
            return
        self._iot_file_path = path
        self._iot_file_data = data
        self.iot_file_label.setText(f"{path.name} · {len(data)} bytes")
        self._update_iot_payload_preview()

    def _format_iot_json(self) -> None:
        source = self.iot_payload_edit.toPlainText()
        try:
            encode_payload("JSON", source)
            value = json.loads(source)
        except ValueError as exc:
            self.iot_payload_status_label.setText(
                f"JSON inválido: {exc}"
            )
            return
        self.iot_payload_edit.setPlainText(
            json.dumps(value, ensure_ascii=False, indent=2)
        )

    def _append_iot_result(self, message: str) -> None:
        stamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.iot_results.appendPlainText(f"[{stamp}] {message}")

    def _start_iot_flow(self, action: str) -> None:
        if self._initializing_at:
            self._append_iot_result(
                "Operación no iniciada: espera a que termine la inicialización AT."
            )
            return
        if not self._is_connected:
            self._append_iot_result("Error: conecta el módem antes de operar.")
            return
        if self._iot_flow_action is not None or self._command_queue.is_busy or self._command_queue.paused:
            self._append_iot_result(
                "Operación no iniciada: espera a que termine la operación AT actual."
            )
            return
        if action == "send":
            try:
                self._iot_payload = encode_payload(
                    self.iot_format_combo.currentText(),
                    self.iot_payload_edit.toPlainText(),
                    self._iot_file_data,
                )
            except (UnicodeEncodeError, ValueError) as exc:
                self._append_iot_result(f"Payload no válido: {exc}")
                return
            if not self._iot_payload or len(self._iot_payload) > UDP_MAX_PAYLOAD_BYTES:
                self._append_iot_result(
                    f"El payload debe tener entre 1 y {UDP_MAX_PAYLOAD_BYTES} bytes."
                )
                return
            self._iot_payload_format = self.iot_format_combo.currentText()

        self._iot_flow_action = action
        self._iot_flow_started_at = datetime.now().astimezone()
        self._iot_flow_responses.clear()
        self._iot_payload_tx_active = False
        self._iot_payload_tx_bytes = 0
        self.iot_state_labels["destination"].setText(
            f"UDP {self.iot_host_edit.text().strip()}:{self.iot_port_spin.value()}"
        )
        self.iot_state_labels["last_operation"].setText(f"{action}: en curso")
        for control in (
            self.iot_host_edit,
            self.iot_port_spin,
            self.iot_context_spin,
            self.iot_socket_spin,
            self.iot_apn_edit,
            self.iot_read_length_spin,
            self.iot_prompt_timeout_spin,
            self.iot_input_time_spin,
            self.iot_response_timeout_spin,
            self.iot_format_combo,
            self.iot_payload_edit,
            self.iot_choose_file_button,
            self.iot_format_json_button,
            self.iot_send_button,
        ):
            control.setEnabled(False)
        for button in self._iot_operation_buttons:
            button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self._append_iot_result(f"Operación {action} iniciada.")

        stage_sets = {
            "state": (
                "registration",
                "attach",
                "pdp_state",
                "app_config",
                "app_context",
                "ip",
                "socket_state",
            ),
            "activate": ("app_config", "app_context"),
            "open": ("registration", "attach", "app_context", "socket_state"),
            "send": ("registration", "attach", "app_context", "socket_state"),
            "close": ("socket_state",),
            "receive": ("registration", "attach", "app_context", "socket_state"),
        }
        self._submit_iot_stages(stage_sets[action])

    def _submit_iot_stages(
        self, stages: Sequence[tuple[str, bytes | AtCommand] | str]
    ) -> None:
        action = self._iot_flow_action
        if action is None:
            return
        timeout_ms = self.command_timeout_spin.value() * 1000
        commands: list[AtCommand] = []
        for stage_item in stages:
            if isinstance(stage_item, tuple):
                stage, command_data = stage_item
            else:
                stage = stage_item
                command_data = _IOT_QUERY_COMMANDS[stage]
            if isinstance(command_data, AtCommand):
                command = command_data
            else:
                encoded = (
                    command_data
                    if isinstance(command_data, bytes)
                    else encode_at_commands(command_data, "CR")[0]
                )
                command = make_at_command(
                    encoded,
                    timeout_ms,
                    purpose=f"iot:{action}:{stage}",
                )
            commands.append(command)
        first = self._command_queue.enqueue(commands, priority=True)
        if first is not None:
            self._write_queued_command(first.data)
        self._update_queue_controls()

    def _on_iot_command_finished(
        self, command: AtCommand, success: bool, *, timed_out: bool = False
    ) -> None:
        purpose = command.purpose
        if purpose is None or not purpose.startswith("iot:"):
            return
        parts = purpose.split(":", 2)
        if len(parts) != 3 or parts[1] != self._iot_flow_action:
            return
        stage = parts[2]
        response_lines = self._command_responses.pop(id(command), [])
        self._iot_flow_responses[stage] = response_lines
        if not success:
            self._command_queue.cancel_pending_purpose("iot:")
            reason = "timeout" if timed_out else "respuesta de error del módem"
            self._finish_iot_flow(False, f"{stage}: {reason}; no se enviarán más comandos.")
            return

        self._update_iot_state_from_response(stage, response_lines)
        if self._command_queue.pending_count:
            return
        self._advance_iot_flow(stage)

    def _update_iot_state_from_response(
        self, stage: str, response_lines: list[bytes]
    ) -> None:
        context_id = self.iot_context_spin.value()
        socket_id = self.iot_socket_spin.value()
        if stage == "registration":
            values = parse_registration(response_lines)
            self.iot_state_labels["registration"].setText(
                values.get("registration", "N/D")
            )
        elif stage == "attach":
            values = parse_command_response("attach", response_lines)
            self.iot_state_labels["attach"].setText(values.get("attach", "N/D"))
        elif stage == "pdp_state":
            values = parse_command_response("pdp", response_lines)
            self.iot_state_labels["pdp_state"].setText(values.get("pdp_state", "N/D"))
        elif stage == "app_config":
            config = parse_application_config(response_lines, context_id)
            self.iot_state_labels["apn"].setText(config.apn if config else "N/D")
        elif stage in {"app_context", "verify_context"}:
            context = parse_application_context(response_lines, context_id)
            self.iot_state_labels["app_context"].setText(
                "N/D"
                if context is None
                else f"Contexto {context.context_id}: "
                f"{'activo' if context.active else 'inactivo'}"
            )
            if context is not None and context.address:
                self.iot_state_labels["ip"].setText(context.address)
        elif stage == "ip":
            values = parse_command_response("ip", response_lines)
            self.iot_state_labels["ip"].setText(values.get("ip", "N/D"))
        elif stage in {"socket_state", "verify_socket"}:
            socket_state = parse_socket_state(response_lines, socket_id)
            state_text = {
                0: "Cerrado",
                1: "Conectado",
                2: "Escuchando",
            }.get(socket_state, f"Estado {socket_state}")
            self.iot_state_labels["socket"].setText(
                f"Socket {socket_id}: {state_text}"
            )
        elif stage == "open":
            self.iot_state_labels["socket"].setText(
                f"Socket {socket_id}: apertura confirmada por módem"
            )
        elif stage == "close":
            self.iot_state_labels["socket"].setText(
                f"Socket {socket_id}: cierre confirmado por módem"
            )

    def _advance_iot_flow(self, stage: str) -> None:
        action = self._iot_flow_action
        if action is None:
            return
        if action == "state":
            self._finish_iot_flow(True, "Consulta de estado finalizada.")
            return
        if action == "activate":
            self._advance_context_activation(stage)
            return
        if action in {"open", "send", "receive"}:
            if stage in {"registration", "attach", "app_context", "socket_state"}:
                if stage != "socket_state":
                    return
                failure = self._iot_precondition_error(
                    require_open_socket=action != "open"
                )
                if failure:
                    self._finish_iot_flow(False, failure)
                    return
                if action == "open":
                    self._submit_iot_stages(
                        [
                            (
                                "open",
                                build_open_socket_command(
                                    self.iot_socket_spin.value(),
                                    self.iot_context_spin.value(),
                                    self.iot_host_edit.text(),
                                    self.iot_port_spin.value(),
                                    self.command_timeout_spin.value() * 1000,
                                ),
                            )
                        ]
                    )
                elif action == "send":
                    assert self._iot_payload is not None
                    self._submit_iot_stages(
                        [
                            (
                                "payload",
                                build_send_command(
                                    self.iot_socket_spin.value(),
                                    self._iot_payload,
                                    self.iot_prompt_timeout_spin.value(),
                                    self.iot_input_time_spin.value(),
                                    self.iot_response_timeout_spin.value(),
                                ),
                            )
                        ]
                    )
                else:
                    self._iot_received_payload.clear()
                    self._carecv_frame_buffer.begin()
                    self._submit_iot_stages(
                        [
                            (
                                "read",
                                build_receive_command(
                                    self.iot_socket_spin.value(),
                                    self.iot_read_length_spin.value(),
                                    self.iot_response_timeout_spin.value(),
                                ),
                            )
                        ]
                    )
                return
            if action == "open" and stage == "open":
                self._submit_iot_stages(("verify_socket",))
                return
            if action == "open" and stage == "verify_socket":
                if parse_socket_state(
                    self._iot_flow_responses.get(stage, []),
                    self.iot_socket_spin.value(),
                ) == 1:
                    self._finish_iot_flow(
                        True,
                        "Socket UDP abierto y verificado por CASTATE.",
                    )
                else:
                    self._finish_iot_flow(
                        False,
                        "CAOPEN terminó, pero CASTATE no confirma el socket conectado.",
                    )
                return
            if action == "send" and stage == "payload":
                expected = len(self._iot_payload or b"")
                if self._iot_payload_tx_bytes != expected:
                    self._finish_iot_flow(
                        False,
                        f"COM confirmó {self._iot_payload_tx_bytes}/{expected} bytes escritos; "
                        "no se declara el envío satisfactorio.",
                    )
                else:
                    self._finish_iot_flow(
                        True,
                        f"COM informó {expected} bytes escritos y el módem respondió OK. "
                        "La recepción remota UDP no está confirmada.",
                    )
                return
            if action == "receive" and stage == "read":
                payload = bytes(self._iot_received_payload)
                rendered = (
                    f"{len(payload)} byte(s): {payload!r}"
                    if payload
                    else "CARECV no informó datos."
                )
                self._finish_iot_flow(
                    True,
                    "CARECV respondió al puerto serie; payload recibido: " + rendered,
                )
                return
        if action == "close":
            if stage == "socket_state":
                state = parse_socket_state(
                    self._iot_flow_responses.get(stage, []),
                    self.iot_socket_spin.value(),
                )
                if state == 0:
                    self._finish_iot_flow(True, "El socket ya estaba cerrado; no se modificó.")
                else:
                    self._submit_iot_stages(
                        [
                            (
                                "close",
                                build_close_socket_command(
                                    self.iot_socket_spin.value(),
                                    self.command_timeout_spin.value() * 1000,
                                ),
                            )
                        ]
                    )
                return
            if stage == "close":
                self._submit_iot_stages(("verify_socket",))
                return
            if stage == "verify_socket":
                if parse_socket_state(
                    self._iot_flow_responses.get(stage, []),
                    self.iot_socket_spin.value(),
                ) == 0:
                    self._finish_iot_flow(True, "Socket cerrado y verificado.")
                else:
                    self._finish_iot_flow(False, "El módem no confirma el cierre del socket.")

    def _advance_context_activation(self, stage: str) -> None:
        context_id = self.iot_context_spin.value()
        context_key = "verify_context" if stage == "verify_context" else "app_context"
        context = parse_application_context(
            self._iot_flow_responses.get(context_key, []), context_id
        )
        config = parse_application_config(
            self._iot_flow_responses.get("app_config", []), context_id
        )
        if stage == "app_context":
            if context is None:
                self._finish_iot_flow(
                    False,
                    f"No se pudo consultar el contexto de aplicaciones {context_id}.",
                )
                return
            if context.active:
                expected_apn = self.iot_apn_edit.text().strip()
                suffix = (
                    " El APN configurado difiere del perfil; no se reconfiguró."
                    if config is not None and config.apn.casefold() != expected_apn.casefold()
                    else ""
                )
                self._finish_iot_flow(
                    True,
                    f"El contexto {context_id} ya está activo; no se repitió la activación.{suffix}",
                )
                return
            if context.status != 0:
                self._finish_iot_flow(
                    False,
                    f"El contexto {context_id} tiene estado {context.status}; "
                    "no se reconfiguró ni se activó.",
                )
                return
            if config is None or config.apn.casefold() != self.iot_apn_edit.text().strip().casefold():
                try:
                    config_command = build_cn_config_command(
                        context_id, self.iot_apn_edit.text()
                    )
                except ValueError as exc:
                    self._finish_iot_flow(False, str(exc))
                    return
                self._submit_iot_stages((("configure", config_command),))
            else:
                self._submit_iot_stages(
                    (("activate", build_cn_activate_command(context_id)),)
                )
        elif stage == "configure":
            self._submit_iot_stages(
                (("activate", build_cn_activate_command(context_id)),)
            )
        elif stage == "activate":
            self._submit_iot_stages(("verify_context",))
        elif stage == "verify_context":
            if context is not None and context.active:
                self._finish_iot_flow(
                    True, f"Contexto {context_id} activado y verificado."
                )
            else:
                self._finish_iot_flow(
                    False,
                    f"El módem respondió a CNACT, pero no confirmó activo el contexto {context_id}.",
                )

    def _iot_precondition_error(self, *, require_open_socket: bool) -> str | None:
        registration = parse_registration(
            self._iot_flow_responses.get("registration", [])
        ).get("registration", "N/D")
        if not registration.startswith("Registrado en"):
            return f"No se inicia la operación: registro móvil actual «{registration}»."
        attached = parse_command_response(
            "attach", self._iot_flow_responses.get("attach", [])
        ).get("attach", "N/D")
        if attached != "Conectado al servicio de paquetes":
            return f"No se inicia la operación: {attached}."
        context = parse_application_context(
            self._iot_flow_responses.get("app_context", []),
            self.iot_context_spin.value(),
        )
        if context is None or not context.active:
            return "No se inicia la operación: el contexto de aplicaciones seleccionado no está activo."
        socket_state = parse_socket_state(
            self._iot_flow_responses.get("socket_state", []),
            self.iot_socket_spin.value(),
        )
        if require_open_socket:
            if socket_state != 1:
                return (
                    f"No se inicia la operación: el socket {self.iot_socket_spin.value()} "
                    "no está conectado."
                )
        elif socket_state != 0:
            return (
                f"No se abre el socket: el identificador {self.iot_socket_spin.value()} "
                f"ya tiene estado {socket_state}."
            )
        return None

    def _finish_iot_flow(self, success: bool, message: str) -> None:
        action = self._iot_flow_action
        if action is None:
            return
        for kind, chunk in self._carecv_frame_buffer.finish():
            if kind == "payload":
                self._iot_received_payload.extend(chunk)
                self._add_event("RX", chunk)
            else:
                self._process_rx_stream(chunk)
        now = datetime.now().astimezone()
        started = self._iot_flow_started_at or now
        duration_ms = max(0, int((now - started).total_seconds() * 1000))
        rendered = message
        if action == "send":
            rendered = (
                f"Destino UDP {self.iot_host_edit.text().strip()}:{self.iot_port_spin.value()} · "
                f"socket {self.iot_socket_spin.value()} · "
                f"{len(self._iot_payload or b'')} bytes · {message}"
            )
        self._append_iot_result(rendered)
        self.iot_state_labels["last_operation"].setText(
            f"{'Correcta' if success else 'Fallida'} · {action} · {duration_ms} ms"
        )
        self._iot_history.append(
            TransmissionRecord(
                timestamp=started,
                protocol="UDP",
                host=self.iot_host_edit.text().strip(),
                port=self.iot_port_spin.value(),
                socket_id=self.iot_socket_spin.value(),
                payload_size=len(self._iot_payload or b"") if action == "send" else 0,
                payload_type=self._iot_payload_format if action == "send" else action,
                result=rendered,
                duration_ms=duration_ms,
                payload=bytes(self._iot_payload) if action == "send" and self._iot_payload else None,
                device_id=self._diagnostic_values.get("iccid", ""),
                sequence_id=self._payload_sequence_id(self._iot_payload)
                if action == "send"
                else None,
                successful=success,
            )
        )
        if hasattr(self, "onence_stats_label"):
            self._update_onence_statistics()
            self._render_onence_history_table()
        self._iot_flow_action = None
        self._iot_flow_started_at = None
        self._iot_payload_tx_active = False
        self._iot_payload = None
        for control in (
            self.iot_host_edit,
            self.iot_port_spin,
            self.iot_context_spin,
            self.iot_socket_spin,
            self.iot_apn_edit,
            self.iot_read_length_spin,
            self.iot_prompt_timeout_spin,
            self.iot_input_time_spin,
            self.iot_response_timeout_spin,
            self.iot_format_combo,
            self.iot_payload_edit,
            self.iot_choose_file_button,
            self.iot_format_json_button,
        ):
            control.setEnabled(True)
        for button in self._iot_operation_buttons:
            button.setEnabled(self._is_connected)
        self.connect_button.setEnabled(True)
        self._toggle_iot_payload_controls()
        self._update_iot_payload_preview()
        self._update_queue_controls()

    @staticmethod
    def _payload_sequence_id(payload: bytes | None) -> str | None:
        if payload is None:
            return None
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        sequence = data.get("seq")
        if isinstance(sequence, (str, int)) and not isinstance(sequence, bool):
            return str(sequence)
        return None

    def _copy_iot_history(self) -> None:
        if not self._iot_history:
            self._append_iot_result("El historial está vacío.")
            return
        lines = [
            f"{record.timestamp:%d/%m/%Y %H:%M:%S} · {record.protocol} "
            f"{record.host}:{record.port} · socket {record.socket_id} · "
            f"{record.payload_size} bytes · {record.payload_type} · "
            f"{record.duration_ms} ms · {record.result}"
            for record in self._iot_history
        ]
        QApplication.clipboard().setText("\n".join(lines))

    def _export_iot_history(self) -> None:
        filename, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Exportar historial IoT",
            str(Path.home() / "tdserial-iot-history.txt"),
            "Texto (*.txt);;JSON (*.json)",
        )
        if not filename:
            return
        path = Path(filename)
        records = []
        for record in self._iot_history:
            values = asdict(record)
            values.pop("payload")
            values.pop("device_id")
            values.pop("sequence_id")
            values.pop("successful")
            values["timestamp"] = record.timestamp.isoformat(timespec="seconds")
            records.append(values)
        try:
            if selected_filter.startswith("JSON") or path.suffix.casefold() == ".json":
                content = json.dumps(records, ensure_ascii=False, indent=2)
            else:
                content = "\n".join(
                    f"{record['timestamp']} · {record['protocol']} "
                    f"{record['host']}:{record['port']} · socket {record['socket_id']} · "
                    f"{record['payload_size']} bytes · {record['payload_type']} · "
                    f"{record['duration_ms']} ms · {record['result']}"
                    for record in records
                )
            path.write_text(content + "\n", encoding="utf-8")
        except OSError as exc:
            logger.exception("No se pudo exportar el historial IoT en %s", path)
            QMessageBox.critical(
                self, "Error al exportar", f"No se pudo guardar el historial.\n{exc}"
            )


    def _build_connection_panel(self) -> QGroupBox:
        group = QGroupBox("Conexión serie")
        layout = QGridLayout(group)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(145)
        self.refresh_button = QPushButton("Actualizar puertos")
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.baud_combo = self._combo(
            ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"],
            str(self._config["baudrate"]),
        )
        self.data_bits_combo = self._combo(["5", "6", "7", "8"], str(self._config["bytesize"]))
        self.parity_combo = self._combo(
            ["Ninguna", "Par", "Impar", "Mark", "Space"],
            {"N": "Ninguna", "E": "Par", "O": "Impar", "M": "Mark", "S": "Space"}.get(
                self._config["parity"], "Ninguna"
            ),
        )
        self.stop_bits_combo = self._combo(["1", "1.5", "2"], str(self._config["stopbits"]))
        self.flow_combo = self._combo(
            ["Ninguno", "XON/XOFF", "RTS/CTS", "DSR/DTR"],
            str(self._config["flow_control"]),
        )
        self.flow_combo.setCurrentText("Ninguno")
        self.flow_combo.setEnabled(False)
        self.flow_combo.setToolTip(
            "El control de flujo está desactivado para mantener la apertura serie pasiva."
        )
        self.connect_button = QPushButton("Conectar")
        self.connect_button.setObjectName("primaryButton")
        self.connect_button.clicked.connect(self._toggle_connection)
        self.initialize_at_check = QCheckBox("Inicializar comunicación AT al conectar")
        self.initialize_at_check.setObjectName("toolbarCheck")
        self.initialize_at_check.setChecked(
            bool(self._config.get("initialize_at_on_connect", False))
        )
        self.initialize_at_check.setToolTip(
            "Desactivado: conexión pasiva, sin comandos AT. Activado: espera "
            "10 segundos para que finalice el arranque LilyGO y ejecuta AT y ATE0 por la cola serie."
        )
        self.status_label = QLabel("● Desconectado")
        self.status_label.setObjectName("disconnectedStatus")

        layout.addWidget(QLabel("Puerto"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1)
        layout.addWidget(self.refresh_button, 0, 2)
        layout.addWidget(QLabel("Velocidad"), 0, 3)
        layout.addWidget(self.baud_combo, 0, 4)
        layout.addWidget(QLabel("Datos"), 0, 5)
        layout.addWidget(self.data_bits_combo, 0, 6)
        layout.addWidget(QLabel("Paridad"), 0, 7)
        layout.addWidget(self.parity_combo, 0, 8)
        layout.addWidget(QLabel("Parada"), 0, 9)
        layout.addWidget(self.stop_bits_combo, 0, 10)
        layout.addWidget(QLabel("Flujo"), 1, 0)
        layout.addWidget(self.flow_combo, 1, 1)
        layout.addWidget(self.initialize_at_check, 1, 2, 1, 3)
        layout.addWidget(self.connect_button, 1, 5, 1, 2)
        layout.addWidget(self.status_label, 1, 7, 1, 4)
        return group

    def _build_monitor_panel(self) -> QGroupBox:
        group = QGroupBox("Monitor de comunicaciones")
        layout = QVBoxLayout(group)
        toolbar = QHBoxLayout()
        self.autoscroll_check = QCheckBox("Autoscroll")
        self.autoscroll_check.setObjectName("toolbarCheck")
        self.autoscroll_check.setChecked(bool(self._config["autoscroll"]))
        self.echo_check = QCheckBox("Activar/desactivar eco")
        self.echo_check.setObjectName("toolbarCheck")
        self.echo_check.setToolTip(
            "Marcado envía ATE1 (activar eco); desmarcado envía ATE0 (desactivar eco). "
            "No se envía ningún comando automáticamente al conectar."
        )
        self.echo_check.setEnabled(self._is_connected)
        self.echo_check.toggled.connect(self._set_modem_echo)
        self.show_details_check = QCheckBox("Mostrar caracteres especiales y tamaño (bytes)")
        self.show_details_check.setObjectName("toolbarCheck")
        self.show_details_check.setChecked(
            bool(self._config.get("show_special_characters_and_size", False))
        )
        self.show_details_check.toggled.connect(self._redraw_console)
        self.pause_button = QPushButton("Pausar visualización")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.clear_button = QPushButton("Limpiar")
        self.clear_button.clicked.connect(self._clear_console)
        self.copy_button = QPushButton("Copiar")
        self.copy_button.clicked.connect(self._copy_console)
        self.save_button = QPushButton("Guardar sesión")
        self.save_button.clicked.connect(self._save_session)
        self.display_combo = self._combo(["ASCII", "HEX"], str(self._config["display_format"]))
        self.display_combo.currentTextChanged.connect(self._redraw_console)
        toolbar.addWidget(self.autoscroll_check)
        toolbar.addWidget(self.echo_check)
        toolbar.addWidget(self.show_details_check)
        toolbar.addWidget(self.pause_button)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("Vista"))
        toolbar.addWidget(self.display_combo)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.save_button)
        toolbar.addWidget(self.clear_button)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addLayout(toolbar)
        layout.addWidget(self.console, 1)
        return group

    def _build_command_panel(self) -> QGroupBox:
        group = QGroupBox("Envío AT / texto directo")
        layout = QGridLayout(group)
        self.command_input = CommandTextEdit()
        self.command_input.setPlaceholderText(
            "Escribe un comando o pega varios (uno por línea). Enter envía; Shift+Enter añade una línea."
        )
        self._adjust_command_input_height()
        self.command_input.send_requested.connect(self._send_command)
        self.terminator_combo = self._combo(list(TERMINATORS), "CR")
        self.terminator_combo.currentTextChanged.connect(self._update_byte_count)
        self.command_input.textChanged.connect(self._update_byte_count)
        self.command_input.textChanged.connect(self._adjust_command_input_height)
        self.direct_send_check = QCheckBox("Enviar texto/caracteres sin terminación")
        self.direct_send_check.setObjectName("toolbarCheck")
        self.direct_send_check.setToolTip(
            "Envía exactamente los bytes UTF-8 escritos, sin añadir CR/LF ni "
            "esperar una respuesta AT terminal."
        )
        self.direct_send_check.toggled.connect(self._update_direct_send_mode)
        self.send_button = QPushButton("Enviar")
        self.send_button.setObjectName("primaryButton")
        self.send_button.clicked.connect(self._send_command)
        self.byte_count_label = QLabel("0 bytes")
        self.byte_count_label.setMinimumWidth(145)
        try:
            timeout_seconds = int(self._config.get("command_timeout_seconds", 10))
        except (TypeError, ValueError):
            timeout_seconds = 10
        self.command_timeout_spin = QSpinBox()
        self.command_timeout_spin.setRange(1, 600)
        self.command_timeout_spin.setSuffix(" s")
        self.command_timeout_spin.setValue(timeout_seconds)
        self.command_timeout_spin.setToolTip(
            "Timeout para cada comando. AT+CAOPEN usa el mismo timeout para ambas etapas."
        )
        error_policies = ["Detener", "Continuar", "Preguntar"]
        configured_policy = str(self._config.get("command_error_policy", "Detener"))
        if configured_policy not in error_policies:
            configured_policy = "Detener"
        self.error_policy_combo = self._combo(error_policies, configured_policy)
        self.error_policy_combo.setToolTip(
            "Acción al recibir ERROR, +CME ERROR o +CMS ERROR."
        )
        self.sequence_controls_widget = QWidget()
        sequence_controls_layout = QHBoxLayout(self.sequence_controls_widget)
        sequence_controls_layout.setContentsMargins(0, 0, 0, 0)
        sequence_controls_layout.addWidget(QLabel("Timeout por comando"))
        sequence_controls_layout.addWidget(self.command_timeout_spin)
        sequence_controls_layout.addWidget(QLabel("Al encontrar error"))
        sequence_controls_layout.addWidget(self.error_policy_combo)
        self.queue_progress_label = QLabel("Cola lista")
        self.queue_progress_label.setMinimumWidth(360)
        sequence_controls_layout.addWidget(self.queue_progress_label, 1)
        self.resume_queue_button = QPushButton("Reanudar")
        self.resume_queue_button.clicked.connect(self._resume_command_queue)
        sequence_controls_layout.addWidget(self.resume_queue_button)
        self.cancel_queue_button = QPushButton("Cancelar secuencia")
        self.cancel_queue_button.clicked.connect(self._cancel_command_queue)
        sequence_controls_layout.addWidget(self.cancel_queue_button)
        self.sequence_controls_widget.setVisible(False)
        layout.addWidget(QLabel("Comandos"), 0, 0)
        layout.addWidget(self.command_input, 0, 1, 1, 6)
        layout.addWidget(QLabel("Terminación por comando"), 0, 7)
        layout.addWidget(self.terminator_combo, 0, 8)
        layout.addWidget(self.byte_count_label, 0, 9)
        layout.addWidget(self.send_button, 0, 10)
        layout.addWidget(self.direct_send_check, 1, 0, 1, 3)
        layout.addWidget(self.sequence_controls_widget, 2, 0, 1, 11)
        self.command_input.textChanged.connect(self._update_queue_controls)
        return group

    def _adjust_command_input_height(self) -> None:
        visible_lines = min(max(self.command_input.document().blockCount(), 1), 5)
        line_height = self.command_input.fontMetrics().lineSpacing()
        height = line_height * visible_lines + 20
        self.command_input.setFixedHeight(height)

    @staticmethod
    def _combo(options: list[str], selected: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(options)
        index = combo.findText(selected)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #0d1117; color: #e6edf3; font-size: 13px; }
            QTabWidget::pane { border: 1px solid #30363d; border-radius: 6px; top: -1px; }
            QTabBar::tab { background: #161b22; color: #8b949e;
                           border: 1px solid #30363d; border-bottom-color: #21262d;
                           border-top-left-radius: 5px; border-top-right-radius: 5px;
                           padding: 7px 12px; margin-right: 3px; }
            QTabBar::tab:selected { background: #21262d; color: #e6edf3;
                                    border-color: #1f6feb; border-bottom-color: #21262d; }
            QTabBar::tab:hover:!selected { color: #c9d1d9; border-color: #484f58; }
            QGroupBox { border: 1px solid #30363d; border-radius: 8px; margin-top: 9px;
                        padding: 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QComboBox, QLineEdit, QTextEdit, QSpinBox { background: #161b22; border: 1px solid #30363d;
                        border-radius: 5px; padding: 6px; selection-background-color: #1f6feb; }
            QPushButton { background: #21262d; border: 1px solid #30363d; border-radius: 5px;
                          padding: 7px 11px; }
            QPushButton:hover { background: #30363d; }
            QPushButton#primaryButton { background: #1f6feb; border-color: #388bfd; color: white; }
            QCheckBox#toolbarCheck { background: #21262d; border: 1px solid #30363d;
                                     border-radius: 5px; padding: 6px 9px; spacing: 6px; }
            QCheckBox#toolbarCheck:hover { background: #30363d; }
            QCheckBox#toolbarCheck::indicator { width: 14px; height: 14px;
                                                background: #161b22; border: 1px solid #6e7681;
                                                border-radius: 3px; }
            QCheckBox#toolbarCheck::indicator:checked { background: #1f6feb;
                                                        border-color: #388bfd; }
            QLabel#disconnectedStatus { color: #8b949e; font-weight: 600; }
            QLabel#connectedStatus { color: #3fb950; font-weight: 600; }
            """
        )

    def refresh_ports(self) -> None:
        current = self.port_combo.currentText() or str(self._config["port"])
        available = {item.device for item in list_ports.comports()}
        available.add("COM6")
        self.port_combo.clear()
        self.port_combo.addItems(sorted(available, key=lambda value: (value != "COM6", value)))
        self.port_combo.setCurrentText(current if current in available else "COM6")
        logger.info("Puertos serie detectados: %s", ", ".join(sorted(available)))

    def _serial_settings(self) -> dict[str, Any]:
        parity = {"Ninguna": "N", "Par": "E", "Impar": "O", "Mark": "M", "Space": "S"}
        flow = self.flow_combo.currentText()
        return {
            "port": self.port_combo.currentText().strip(),
            "baudrate": int(self.baud_combo.currentText()),
            "bytesize": int(self.data_bits_combo.currentText()),
            "parity": parity[self.parity_combo.currentText()],
            "stopbits": float(self.stop_bits_combo.currentText()),
            "flow_control": flow,
            "xonxoff": flow == "XON/XOFF",
            "rtscts": flow == "RTS/CTS",
            "dsrdtr": flow == "DSR/DTR",
        }

    def _toggle_connection(self) -> None:
        if self._iot_flow_action is not None:
            self._append_iot_result(
                "No se puede desconectar durante una operación IoT activa."
            )
            return
        if self._is_connected:
            self.close_requested.emit()
            self.connect_button.setEnabled(False)
            return
        if self._connecting:
            return
        try:
            settings = self._serial_settings()
        except (ValueError, KeyError) as exc:
            self._on_error(f"Configuración serie no válida: {exc}")
            return
        if not settings["port"]:
            self._on_error("Selecciona un puerto COM antes de conectar.")
            return
        self._connecting = True
        self.connect_button.setEnabled(False)
        self.initialize_at_check.setEnabled(False)
        self.open_requested.emit(settings)

    def _on_connection_changed(self, connected: bool) -> None:
        logger.info("Cambio de estado de conexión serie: %s", connected)
        if not connected:
            if self._initializing_at:
                logger.info("Inicialización AT cancelada por desconexión")
                self._add_event(
                    "SYSTEM",
                    "Inicialización AT cancelada por desconexión".encode("utf-8"),
                )
            self._is_connected = False
            self._command_timeout_timer.stop()
            self._initialization_timer.stop()
            self._initializing_at = False
            self._automatic_monitor_timer.stop()
            self.automatic_monitor_check.blockSignals(True)
            self.automatic_monitor_check.setChecked(False)
            self.automatic_monitor_check.blockSignals(False)
            self._set_monitor_status(False)
            self._auto_monitor_running = False
            self._auto_monitor_remaining = 0
            self._diagnostic_manual_remaining = 0
            self._diagnostic_manual_total = 0
            self._diagnostic_manual_completed = 0
            self._command_responses.clear()
            if self._error_dialog is not None:
                self._error_dialog.close()
                self._error_dialog.deleteLater()
                self._error_dialog = None
            discarded = self._command_queue.clear()
            if self._iot_flow_action is not None:
                self._finish_iot_flow(
                    False, "Conexión serie perdida; operación detenida."
                )
            else:
                self._carecv_frame_buffer.finish()
            self._flush_rx_buffer()
            if discarded:
                self._add_event(
                    "SYSTEM",
                    f"Secuencia cancelada al desconectar ({discarded} comando(s) pendiente(s))".encode(
                        "utf-8"
                    ),
                )
            self._queue_status = "Desconectada"
            self._update_queue_controls()
        self._is_connected = connected
        self._connecting = False
        if connected:
            self.automatic_monitor_check.blockSignals(True)
            self.automatic_monitor_check.setChecked(False)
            self.automatic_monitor_check.blockSignals(False)
            self._set_monitor_status(False)
            self.echo_check.blockSignals(True)
            self.echo_check.setChecked(False)
            self.echo_check.blockSignals(False)
            if self.initialize_at_check.isChecked():
                self._initializing_at = True
                self.status_label.setText("Puerto conectado — inicializando AT")
                self.status_label.setObjectName("connectedStatus")
                logger.info(
                    "Inicialización AT solicitada; comenzará en %d ms",
                    self.AT_INITIALIZATION_DELAY_MS,
                )
                self._add_event(
                    "SYSTEM",
                    f"Inicialización AT programada en {self.AT_INITIALIZATION_DELAY_MS // 1000} s".encode(
                        "utf-8"
                    ),
                )
                self._initialization_timer.start(self.AT_INITIALIZATION_DELAY_MS)
            else:
                self.status_label.setText("Puerto conectado — modo pasivo")
                self.status_label.setObjectName("connectedStatus")
                logger.info("Puerto conectado en modo pasivo; no se enviarán comandos AT")
                self._add_event("SYSTEM", "Puerto conectado — modo pasivo".encode("utf-8"))
        else:
            self._initializing_at = False
            self.status_label.setText("● Desconectado")
            self.status_label.setObjectName("disconnectedStatus")
        self.echo_check.setEnabled(
            connected and not self._command_queue.paused and not self._initializing_at
        )
        self.command_input.setEnabled(connected and not self._initializing_at)
        self.send_button.setEnabled(
            connected and not self._command_queue.paused and not self._initializing_at
        )
        self.direct_send_check.setEnabled(connected and not self._initializing_at)
        self.terminator_combo.setEnabled(
            connected
            and not self.direct_send_check.isChecked()
        )
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Desconectar" if connected else "Conectar")
        self.initialize_at_check.setEnabled(not connected)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._add_event("SYSTEM", b"Puerto serie conectado" if connected else b"Puerto serie desconectado")
        if connected and self._initializing_at:
            self.automatic_monitor_check.setEnabled(False)
        else:
            self.automatic_monitor_check.setEnabled(connected)
        self._update_queue_controls()

    def _start_at_initialization(self) -> None:
        if not self._is_connected or not self._initializing_at:
            return
        if self._command_queue.is_busy or self._command_queue.paused:
            logger.warning("Inicialización AT cancelada porque la cola no está disponible")
            self._finish_at_initialization(False, "La cola AT no está disponible.")
            return
        timeout_ms = self.command_timeout_spin.value() * 1000
        commands = [
            make_at_command(b"AT\r", timeout_ms, purpose="init:verify"),
            make_at_command(b"ATE0\r", timeout_ms, purpose="init:echo"),
        ]
        logger.info("Inicio de inicialización AT: verificación AT seguida de ATE0")
        self._add_event(
            "SYSTEM", "Inicio de inicialización AT: AT y ATE0".encode("utf-8")
        )
        first = self._command_queue.enqueue(commands, priority=True)
        if first is not None:
            self._write_queued_command(first.data)

    def _on_initialization_command_finished(
        self, command: AtCommand, success: bool, *, timed_out: bool = False
    ) -> None:
        purpose = command.purpose
        if purpose is None or not purpose.startswith("init:"):
            return
        if not success:
            self._command_queue.cancel_pending_purpose("init:")
            if timed_out and self._command_queue.paused:
                self._command_queue.resume()
            reason = "timeout" if timed_out else "respuesta de error del módem"
            self._finish_at_initialization(False, f"Inicialización AT fallida: {reason}.")
            return
        if purpose == "init:echo":
            self._finish_at_initialization(True, "Inicialización AT completada.")

    def _finish_at_initialization(self, success: bool, message: str) -> None:
        self._initialization_timer.stop()
        self._initializing_at = False
        logger.info("%s", message)
        self._add_event("SYSTEM" if success else "ERROR", message.encode("utf-8"))
        if self._is_connected:
            self.status_label.setText(
                "Puerto conectado — AT inicializado"
                if success
                else "Puerto conectado — inicialización AT fallida"
            )
            self.status_label.setObjectName(
                "connectedStatus" if success else "disconnectedStatus"
            )
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            self.automatic_monitor_check.setEnabled(True)
        self._update_queue_controls()

    def _on_transmitted(self, data: bytes) -> None:
        if self._iot_payload_tx_active:
            self._iot_payload_tx_bytes += len(data)
        active = self._command_queue.active_spec
        if (
            active is not None
            and active.purpose is not None
            and active.purpose.startswith(("init:", "auto:"))
        ):
            logger.info(
                "Comando automático transmitido (%s): %s",
                active.purpose,
                data.hex(" "),
            )
        self._add_event("TX", data)

    def _on_received(self, data: bytes) -> None:
        if self._carecv_frame_buffer.enabled:
            chunks = self._carecv_frame_buffer.feed(data)
        else:
            chunks = [("stream", data)]
        for kind, chunk in chunks:
            if kind == "payload":
                self._iot_received_payload.extend(chunk)
                self._add_event("RX", chunk)
                continue
            self._process_rx_stream(chunk)

    def _process_rx_stream(self, data: bytes) -> None:
        for line in self._rx_line_buffer.feed(data):
            self._process_rx_line(line)
        prompt = self._rx_line_buffer.flush_prompt()
        if prompt:
            self._process_rx_line(prompt)
        if self._rx_line_buffer.has_pending:
            self._rx_flush_timer.start()
        else:
            self._rx_flush_timer.stop()

    def _flush_incomplete_rx_line(self) -> None:
        line = self._rx_line_buffer.flush_ready()
        if line:
            self._process_rx_line(line)

    def _flush_rx_buffer(self) -> None:
        line = self._rx_line_buffer.flush()
        if line:
            self._process_rx_line(line)

    def _process_rx_line(self, line: bytes) -> None:
        self._add_event("RX", line)
        active = self._command_queue.active_spec
        if active is not None and active.purpose is not None:
            self._command_responses.setdefault(id(active), []).append(line)
        complete = line.endswith((b"\r", b"\n"))
        event = self._command_queue.accept_line(line, complete=complete)
        if event is None:
            return
        if event.kind == "prompt":
            interaction = event.command.interaction
            if interaction is not None:
                self._command_timeout_timer.start(interaction.response_timeout_ms)
            if event.payload:
                self._iot_payload_tx_active = bool(
                    event.command.purpose
                    and event.command.purpose.startswith("iot:send:")
                )
                if self._iot_payload_tx_active:
                    self._iot_payload_tx_bytes = 0
                self.write_requested.emit(event.payload)
                self._queue_status = "Prompt detectado; transmitiendo payload por el puerto serie"
                self._add_event(
                    "SYSTEM",
                    b"Prompt interactivo detectado; transmitiendo payload por el puerto serie",
                )
            return
        self._command_timeout_timer.stop()
        self._on_diagnostic_command_finished(
            event.command, event.kind == "success"
        )
        self._on_initialization_command_finished(
            event.command, event.kind == "success"
        )
        self._on_iot_command_finished(
            event.command, event.kind == "success"
        )
        if event.kind == "error":
            command = event.command.data.rstrip(b"\r\n").decode("ascii", errors="replace")
            response = event.response.decode("ascii", errors="replace").strip()
            self._add_event(
                "ERROR",
                f"Respuesta de error a {command!r}: {response}".encode("utf-8"),
            )
            self._handle_command_error(event)
            return
        self._queue_status = "Comando completado"
        self._send_next_queued_command()

    def _on_error(self, message: str) -> None:
        logger.error("%s", message)
        self._add_event("ERROR", message.encode("utf-8", errors="replace"))
        QMessageBox.warning(self, "TdSerial IoT", message)

    def _send_command(self) -> None:
        if self._initializing_at:
            self._on_error("Espera a que termine la inicialización de comunicación AT.")
            return
        if self._iot_flow_action is not None:
            self._on_error("Espera a que termine la operación de Comunicaciones IoT.")
            return
        text = self.command_input.toPlainText()
        if self.direct_send_check.isChecked():
            if not text:
                return
            if not self._is_connected:
                self._on_error("No hay ningún puerto serie conectado.")
                return
            if self._command_queue.is_busy or self._command_queue.paused:
                self._on_error(
                    "El envío directo no puede intercalarse con una secuencia AT activa."
                )
                return
            payload = text.encode("utf-8")
            self.write_requested.emit(payload)
            self.command_input.clear()
            return
        commands = [line.strip() for line in text.splitlines() if line.strip()]
        if not commands:
            return
        try:
            encoded_commands = encode_at_commands(text, self.terminator_combo.currentText())
        except (UnicodeEncodeError, ValueError) as exc:
            self._on_error(f"Los comandos deben ser texto ASCII y usar una terminación válida. {exc}")
            return
        if not self._is_connected:
            self._on_error("No hay ningún puerto serie conectado.")
            return
        if self._command_queue.paused:
            self._on_error("La secuencia está pausada. Reanúdala o cancélala antes de enviar más comandos.")
            return
        self.command_input.remember(commands)
        timeout_ms = self.command_timeout_spin.value() * 1000
        specs = [make_at_command(command, timeout_ms) for command in encoded_commands]
        if len(specs) > 1:
            self._manual_sequence_active = True
        first_command = self._command_queue.enqueue(specs, priority=True)
        if first_command is not None:
            self._queue_status = "Enviando secuencia"
            self._write_queued_command(first_command.data)
        self._update_queue_controls()
        self.command_input.clear()

    def _update_byte_count(self) -> None:
        if self.direct_send_check.isChecked():
            data = self.command_input.toPlainText().encode("utf-8")
            self.byte_count_label.setText(
                f"{len(data)} byte(s) · envío directo"
            )
            return
        try:
            commands = encode_at_commands(
                self.command_input.toPlainText(),
                self.terminator_combo.currentText(),
            )
        except (UnicodeEncodeError, ValueError):
            self.byte_count_label.setText("Texto no ASCII")
            return
        total_bytes = sum(len(command) for command in commands)
        self.byte_count_label.setText(f"{total_bytes} bytes · {len(commands)} comando(s)")

    def _update_direct_send_mode(self, enabled: bool) -> None:
        self.terminator_combo.setEnabled(self._is_connected and not enabled)
        self._update_byte_count()
        self._update_queue_controls()

    def _write_queued_command(self, command: bytes) -> None:
        active = self._command_queue.active_spec
        if active is not None:
            self._command_timeout_timer.start(active.timeout_ms)
        self._queue_status = "Esperando respuesta"
        self.write_requested.emit(command)
        self._update_queue_controls()

    def _on_command_timeout(self) -> None:
        timed_out = self._command_queue.timeout()
        if timed_out is None:
            return
        logger.warning(
            "Timeout esperando respuesta completa al comando %r",
            timed_out.data.rstrip(b"\r\n"),
        )
        self._on_diagnostic_command_finished(timed_out, False, timed_out=True)
        self._on_initialization_command_finished(timed_out, False, timed_out=True)
        self._on_iot_command_finished(timed_out, False, timed_out=True)
        if timed_out.purpose is not None and timed_out.purpose.startswith("auto:"):
            self.automatic_monitor_check.setChecked(False)
        command = timed_out.data.rstrip(b"\r\n").decode("ascii", errors="replace")
        seconds = timed_out.timeout_ms / 1000
        initialization_timeout = bool(
            timed_out.purpose and timed_out.purpose.startswith("init:")
        )
        self._queue_status = (
            "Inicialización AT fallida; cola disponible"
            if initialization_timeout
            else "Pausada por timeout"
        )
        timeout_message = (
            f"Timeout esperando respuesta completa a {command!r} ({seconds:g} s). "
            + (
                "La cola vuelve a estar disponible."
                if initialization_timeout
                else "La cola está pausada."
            )
        )
        self._add_event("ERROR", timeout_message.encode("utf-8"))
        if self._command_queue.cancel_requested:
            self._command_queue.finish_cancellation()
            self._queue_status = "Secuencia cancelada tras finalizar el comando actual"
        self._update_queue_controls()

    def _send_next_queued_command(self) -> None:
        next_command = self._command_queue.start_next()
        if next_command is None:
            if self._command_queue.active_spec is not None:
                self._update_queue_controls()
                return
            if self._command_queue.cancel_requested:
                self._command_queue.finish_cancellation()
                self._queue_status = "Secuencia cancelada tras finalizar el comando actual"
            elif self._command_queue.paused:
                self._update_queue_controls()
                return
            else:
                self._queue_status = (
                    "Secuencia finalizada con errores"
                    if self._command_queue.error_count
                    else "Secuencia finalizada"
                )
                self._manual_sequence_active = False
            self._update_queue_controls()
            return
        self._write_queued_command(next_command.data)

    def _handle_command_error(self, event: QueueEvent) -> None:
        if event.command.purpose and event.command.purpose.startswith("auto:"):
            self._queue_status = "Error de monitorización; continuando"
            self._send_next_queued_command()
            return
        if self._command_queue.cancel_requested:
            self._command_queue.finish_cancellation()
            self._queue_status = "Secuencia cancelada tras finalizar el comando actual"
            self._update_queue_controls()
            return
        policy = self.error_policy_combo.currentText()
        if self._command_queue.pending_count == 0:
            self._queue_status = "Secuencia finalizada con errores"
            self._manual_sequence_active = False
            self._update_queue_controls()
            return
        if policy == "Detener":
            self._command_queue.pause()
            self._queue_status = "Pausada por respuesta de error"
            self._update_queue_controls()
        elif policy == "Continuar":
            self._queue_status = "Error; continuando"
            self._send_next_queued_command()
        else:
            self._command_queue.pause()
            self._queue_status = "Esperando decisión tras error"
            self._update_queue_controls()
            self._ask_continue_after_error(event)

    def _ask_continue_after_error(self, event: QueueEvent) -> None:
        command = event.command.data.rstrip(b"\r\n").decode("ascii", errors="replace")
        response = event.response.decode("ascii", errors="replace").strip()
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Error en la secuencia AT")
        dialog.setText(f"{command}\nRespuesta: {response}")
        dialog.setInformativeText("¿Deseas continuar con el siguiente comando?")
        continue_button = dialog.addButton("Continuar", QMessageBox.ButtonRole.AcceptRole)
        stop_button = dialog.addButton("Detener", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(stop_button)
        self._error_dialog = dialog

        def finish_decision(button: QAbstractButton) -> None:
            self._error_dialog = None
            if button is continue_button:
                self._queue_status = "Continuando tras error"
                self._resume_command_queue()
            else:
                self._queue_status = "Pausada por respuesta de error"
                self._update_queue_controls()
            dialog.deleteLater()

        dialog.buttonClicked.connect(finish_decision)
        dialog.open()

    def _resume_command_queue(self) -> None:
        if not self._is_connected:
            self._on_error("Conecta el puerto antes de reanudar la secuencia.")
            return
        next_command = self._command_queue.resume()
        if next_command is not None:
            self._queue_status = "Reanudando secuencia"
            self._write_queued_command(next_command.data)
        else:
            self._queue_status = (
                "Secuencia cancelada"
                if self._command_queue.cancel_requested
                else (
                    "Secuencia finalizada con errores"
                    if self._command_queue.error_count
                    else "Secuencia finalizada"
                )
            )
            self._update_queue_controls()

    def _cancel_command_queue(self) -> None:
        cancelled = self._command_queue.cancel_pending()
        active = self._command_queue.active_spec
        self._diagnostic_manual_remaining = (
            1
            if active is not None
            and active.purpose is not None
            and active.purpose.startswith("diag:")
            else 0
        )
        self._diagnostic_manual_total = self._diagnostic_manual_remaining
        self._diagnostic_manual_completed = 0
        self._auto_monitor_remaining = (
            1
            if active is not None
            and active.purpose is not None
            and active.purpose.startswith("auto:")
            else 0
        )
        self._auto_monitor_running = self._auto_monitor_remaining > 0
        if self._diagnostic_manual_remaining == 0:
            self._diagnostic_manual_failed = False
        elif (
            active is not None
            and active.purpose is not None
            and active.purpose.startswith("diag:")
        ):
            self._diagnostic_manual_failed = True
        if (
            active is not None
            and active.purpose is not None
            and active.purpose.startswith("auto:")
        ):
            self._auto_monitor_failed = True
        self._queue_status = (
            f"Cancelada; {cancelled} comando(s) pendiente(s) descartado(s)"
        )
        if self._command_queue.active_spec is not None:
            self._queue_status += "; esperando fin del comando actual"
        else:
            self._command_timeout_timer.stop()
        self._update_queue_controls()

    def _update_queue_controls(self) -> None:
        if not hasattr(self, "queue_progress_label"):
            return
        entered_commands = [
            line for line in self.command_input.toPlainText().splitlines() if line.strip()
        ]
        has_entered_sequence = len(entered_commands) > 1
        active = self._command_queue.active_spec
        current = (
            active.data.rstrip(b"\r\n").decode("ascii", errors="replace")
            if active is not None
            else "—"
        )
        summary = (
            f"Actual: {current} | ejecutados: {self._command_queue.executed_count}/"
            f"{self._command_queue.total_count} | pendientes: "
            f"{self._command_queue.pending_count} | completados: "
            f"{self._command_queue.completed_count} | errores: "
            f"{self._command_queue.error_count}"
        )
        if self._command_queue.cancelled_count:
            summary += f" | cancelados: {self._command_queue.cancelled_count}"
        self.queue_progress_label.setText(f"{self._queue_status} · {summary}")
        self.resume_queue_button.setEnabled(
            self._is_connected
            and self._command_queue.paused
            and not self._command_queue.cancel_requested
        )
        self.cancel_queue_button.setEnabled(
            self._is_connected
            and (
                self._command_queue.paused
                or self._command_queue.active_spec is not None
                or self._command_queue.pending_count > 0
            )
            and not self._command_queue.cancel_requested
        )
        operation_idle = self._iot_flow_action is None
        input_available = (
            self._is_connected and operation_idle and not self._initializing_at
        )
        self.command_input.setEnabled(input_available)
        self.direct_send_check.setEnabled(input_available)
        self.terminator_combo.setEnabled(
            input_available and not self.direct_send_check.isChecked()
        )
        self.send_button.setEnabled(
            input_available
            and (
                not self._command_queue.paused
                if not self.direct_send_check.isChecked()
                else not self._command_queue.is_busy
                and not self._command_queue.paused
            )
        )
        self.echo_check.setEnabled(
            input_available and not self._command_queue.paused
        )
        self.sequence_controls_widget.setVisible(
            has_entered_sequence
            or self._manual_sequence_active
            or self._diagnostic_manual_remaining > 1
            or self._command_queue.paused
        )

    def _set_modem_echo(self, enabled: bool) -> None:
        if (
            not self._is_connected
            or self._initializing_at
            or self._command_queue.paused
            or self._iot_flow_action is not None
        ):
            return
        command = b"ATE1" if enabled else b"ATE0"
        encoded = encode_at_commands(command.decode("ascii"), self.terminator_combo.currentText())
        first_command = self._command_queue.enqueue(
            [make_at_command(encoded[0], self.command_timeout_spin.value() * 1000)],
            priority=True,
        )
        if first_command is not None:
            self._write_queued_command(first_command.data)
        else:
            self._update_queue_controls()

    def _start_diagnostic(self, keys: Sequence[str]) -> None:
        if self._initializing_at:
            self.diagnostic_progress_label.setText(
                "Espera a que termine la inicialización de comunicación AT."
            )
            return
        if self._iot_flow_action is not None:
            self.diagnostic_progress_label.setText(
                "Espera a que termine la operación de Comunicaciones IoT."
            )
            return
        if not self._is_connected:
            self.diagnostic_progress_label.setText("Conecta el módem para consultar el diagnóstico.")
            return
        if self._command_queue.paused:
            self.diagnostic_progress_label.setText(
                "La cola está pausada; reanúdala o cancélala desde Monitor serie."
            )
            return
        timeout_ms = self.command_timeout_spin.value() * 1000
        commands = [
            make_at_command(
                encode_at_commands(SIM7080G_COMMANDS[key], "CR")[0],
                timeout_ms,
                purpose=f"diag:{key}",
            )
            for key in keys
        ]
        self._diagnostic_manual_remaining += len(commands)
        if self._diagnostic_manual_remaining == len(commands):
            self._diagnostic_manual_failed = False
            self._diagnostic_manual_total = 0
            self._diagnostic_manual_completed = 0
        self._diagnostic_manual_total += len(commands)
        self.diagnostic_progress_label.setText(
            f"Diagnóstico en curso: {len(commands)} consulta(s) solicitada(s)"
        )
        for key in keys:
            self._set_diagnostic_value(key, "Consultando…")
        first_command = self._command_queue.enqueue(commands, priority=True)
        if first_command is not None:
            self._write_queued_command(first_command.data)
        self._update_queue_controls()

    def _diagnostic_report_values(self) -> dict[str, str]:
        timestamp = (
            self._last_diagnostic_at.strftime("%d/%m/%Y %H:%M:%S")
            if self._last_diagnostic_at is not None
            else "N/D"
        )
        values = {"Fecha y hora": timestamp}
        for key, title in self._diagnostic_field_titles.items():
            value = self._diagnostic_values.get(key, "N/D")
            values[title] = value

        extra_fields = (
            ("modem_info", "Información ATI"),
            ("access_technology", "Tecnología de acceso reportada"),
            ("cpsi_state", "Estado CPSI"),
            ("cpsi_mcc_mnc", "MCC/MNC reportado por CPSI"),
        )
        for key, title in extra_fields:
            if key in self._diagnostic_values and key not in self._diagnostic_field_titles:
                values[title] = self._diagnostic_values[key]
        return values

    def _copy_diagnostic(self) -> None:
        values = self._diagnostic_report_values()
        lines = ["TdSerial IoT — Diagnóstico SIM7080G"]
        lines.extend(f"{title}: {value}" for title, value in values.items())
        QApplication.clipboard().setText("\n".join(lines))

    def _export_diagnostic(self) -> None:
        filename, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Exportar diagnóstico",
            str(Path.home() / "tdserial-iot-diagnostic.txt"),
            "Texto (*.txt);;JSON (*.json)",
        )
        if not filename:
            return
        path = Path(filename)
        values = self._diagnostic_report_values()
        try:
            if selected_filter.startswith("JSON") or path.suffix.casefold() == ".json":
                content = json.dumps(
                    {
                        "application": "TdSerial IoT",
                        "modem": "SIM7080G",
                        "diagnostic": values,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                content = "\n".join(
                    ["TdSerial IoT — Diagnóstico SIM7080G"]
                    + [f"{title}: {value}" for title, value in values.items()]
                )
            path.write_text(content + "\n", encoding="utf-8")
        except OSError as exc:
            logger.exception("No se pudo exportar el diagnóstico en %s", path)
            QMessageBox.critical(
                self, "Error al exportar", f"No se pudo guardar el diagnóstico.\n{exc}"
            )

    def _on_diagnostic_command_finished(
        self, command: AtCommand, success: bool, *, timed_out: bool = False
    ) -> None:
        purpose = command.purpose
        if purpose is None:
            return
        if purpose.startswith("diag:"):
            key = purpose.removeprefix("diag:")
        elif purpose.startswith("auto:"):
            key = purpose.removeprefix("auto:")
        else:
            return
        response_lines = self._command_responses.pop(id(command), [])

        if success:
            updates = parse_command_response(key, response_lines)
            self._diagnostic_values.update(updates)
            if (
                purpose == "diag:iccid"
                and "iccid" in updates
                and hasattr(self, "onence_iccid_edit")
            ):
                self.onence_iccid_edit.setText(updates["iccid"])
            for field_key, value in updates.items():
                if field_key in {"imei", "iccid", "imsi"}:
                    continue
                self._set_diagnostic_value(field_key, value)
            self._render_identity_fields()
            self._update_derived_diagnostic_values()
        else:
            if purpose.startswith("diag:"):
                self._diagnostic_manual_failed = True
            message = "Timeout" if timed_out else "Error de lectura"
            for field_key in self._diagnostic_fields_for_command(key):
                if field_key not in {"imei", "iccid", "imsi"}:
                    self._set_diagnostic_value(field_key, message)
            self._update_derived_diagnostic_values()

        if purpose.startswith("diag:"):
            self._diagnostic_manual_completed += 1
            self._diagnostic_manual_remaining = max(
                0, self._diagnostic_manual_remaining - 1
            )
            if self._diagnostic_manual_remaining:
                self.diagnostic_progress_label.setText(
                    f"Diagnóstico en curso: {self._diagnostic_manual_completed}/"
                    f"{self._diagnostic_manual_total} consultas"
                )
            if self._diagnostic_manual_remaining == 0:
                self.diagnostic_progress_label.setText(
                    "Diagnóstico completo finalizado"
                    if not self._diagnostic_manual_failed
                    else "Diagnóstico finalizado con consultas fallidas"
                )
                if not self._diagnostic_manual_failed:
                    self._update_last_diagnostic_timestamp()
        else:
            if not success:
                self._auto_monitor_failed = True
            self._auto_monitor_remaining = max(0, self._auto_monitor_remaining - 1)
            if timed_out:
                self._command_queue.cancel_pending_purpose("auto:")
                self._auto_monitor_remaining = 0
                self._auto_monitor_running = False
            elif self._auto_monitor_remaining == 0:
                completed_successfully = (
                    self._auto_monitor_running
                    and not self._auto_monitor_failed
                    and self.automatic_monitor_check.isChecked()
                )
                self._auto_monitor_running = False
                if completed_successfully:
                    self._update_last_diagnostic_timestamp()
                    self.diagnostic_progress_label.setText("Monitorización actualizada")
                elif self.automatic_monitor_check.isChecked():
                    self.diagnostic_progress_label.setText(
                        "Monitorización finalizada con consultas fallidas"
                    )
                else:
                    self._set_monitor_status(False)

    def _update_last_diagnostic_timestamp(self) -> None:
        self._last_diagnostic_at = datetime.now()
        self.last_diagnostic_label.setText(
            "Último diagnóstico: "
            + self._last_diagnostic_at.strftime("%d/%m/%Y %H:%M:%S")
        )

    @staticmethod
    def _diagnostic_fields_for_command(key: str) -> tuple[str, ...]:
        fields = {
            "ati": ("modem_info",),
            "manufacturer": ("manufacturer",),
            "model": ("model",),
            "firmware": ("firmware",),
            "imei": ("imei",),
            "iccid": ("iccid",),
            "imsi": ("imsi",),
            "sim": ("sim",),
            "registration": ("registration", "roaming", "cell"),
            "operator": ("operator", "operator_code", "access_technology"),
            "cpsi": (
                "technology",
                "cpsi_state",
                "band",
                "cell",
                "cpsi_mcc_mnc",
                "rssi_measured",
                "rsrp",
                "rsrq",
                "rssnr",
                "sinr",
                "signal_quality",
            ),
            "csq": ("csq", "rssi"),
            "attach": ("attach",),
            "pdp": ("pdp_state",),
            "contexts": ("pdp", "apn"),
            "ip": ("ip",),
            "app_context": ("app_context",),
            "sockets": ("sockets",),
        }
        return fields.get(key, ())

    def _set_diagnostic_value(self, key: str, value: str) -> None:
        self._diagnostic_values[key] = value
        label = self._diagnostic_fields.get(key)
        if label is None:
            return
        color = self._diagnostic_color(key, value)
        label.setText(f"● {value}")
        label.setStyleSheet(f"color: {color};")

    @staticmethod
    def _diagnostic_color(key: str, value: str) -> str:
        text = value.casefold()
        if value in {"N/D", "Consultando…"} or "desconocido" in text:
            return "#8b949e" if value != "Consultando…" else "#d29922"
        if (
            "error" in text
            or "rechazado" in text
            or "no registrado" in text
            or "desconectado" in text
        ):
            return "#ff6b6b"
        if key == "sockets" and "sin conexiones" in text:
            return "#8b949e"
        if "no disponible" in text:
            return "#ff6b6b"
        if "buscando" in text or "requerido" in text or "inactivo" in text or "débil" in text:
            return "#d29922"
        if key in {"signal_quality", "rsrp_quality", "rsrq_quality", "sinr_quality"}:
            if "sin servicio" in text or "deficiente" in text:
                return "#ff6b6b"
            if "excelente" in text:
                return "#238636"
            if "buena" in text:
                return "#3fb950"
            if "aceptable" in text or "parcial" in text:
                return "#d29922"
            if "desconocida" in text:
                return "#8b949e"
        if key == "coverage":
            if "buena" in text:
                return "#3fb950"
            if "débil" in text or "muy débil" in text:
                return "#d29922"
        if key in {
            "sim",
            "registration",
            "attach",
            "sockets",
            "app_context",
            "pdp_state",
        } and (
            "preparada" in text
            or "registrado" in text
            or "conectado" in text
            or "activo" in text
        ):
            return "#3fb950"
        return "#e6edf3"

    def _render_identity_fields(self) -> None:
        for key in ("manufacturer", "model", "firmware", "imei", "iccid", "imsi"):
            if key in self._diagnostic_values:
                self._set_diagnostic_value(key, self._diagnostic_values[key])

    def _update_derived_diagnostic_values(self) -> None:
        mcc_mnc = self._diagnostic_values.get("cpsi_mcc_mnc")
        if mcc_mnc and mcc_mnc != "N/D":
            self._set_diagnostic_value("operator_code", mcc_mnc)
        technology = self._diagnostic_values.get("technology")
        normalized_technology = normalize_signal_technology(technology)
        quality_title = {
            "NB-IoT": "Calidad de señal NB-IoT",
            "LTE-M": "Calidad de señal LTE-M",
        }.get(normalized_technology, "Calidad de señal")
        self._diagnostic_field_titles["signal_quality"] = quality_title
        title_label = self._diagnostic_title_labels.get("signal_quality")
        if title_label is not None:
            title_label.setText(quality_title)
        signal_quality = classify_signal_quality(
            self._diagnostic_values.get("rsrp"),
            self._diagnostic_values.get("rsrq"),
            self._diagnostic_values.get("sinr"),
            self._diagnostic_values.get("registration", ""),
            technology,
            self._signal_quality_thresholds,
        )
        for key, value in signal_quality.items():
            self._set_diagnostic_value(key, value)

    def _toggle_automatic_monitor(self, enabled: bool) -> None:
        if not enabled:
            self._automatic_monitor_timer.stop()
            cancelled = self._command_queue.cancel_pending_purpose("auto:")
            self._auto_monitor_remaining = max(
                0, self._auto_monitor_remaining - cancelled
            )
            self._auto_monitor_running = False
            logger.info(
                "Monitorización automática desactivada; %d consulta(s) pendiente(s) cancelada(s)",
                cancelled,
            )
            self._set_monitor_status(False)
            return
        if self._command_queue.paused:
            self.diagnostic_progress_label.setText(
                "La cola AT está pausada; reanúdala antes de activar la monitorización."
            )
            self.automatic_monitor_check.blockSignals(True)
            self.automatic_monitor_check.setChecked(False)
            self.automatic_monitor_check.blockSignals(False)
            self._set_monitor_status(False)
            return
        if self._initializing_at:
            logger.info("Monitorización automática rechazada durante inicialización AT")
            self.automatic_monitor_check.blockSignals(True)
            self.automatic_monitor_check.setChecked(False)
            self.automatic_monitor_check.blockSignals(False)
            self._set_monitor_status(False)
            self.diagnostic_progress_label.setText(
                "Espera a que termine la inicialización de comunicación AT."
            )
            return
        if not self._is_connected:
            self.diagnostic_progress_label.setText(
                "Conecta el módem antes de activar la monitorización."
            )
            self.automatic_monitor_check.blockSignals(True)
            self.automatic_monitor_check.setChecked(False)
            self.automatic_monitor_check.blockSignals(False)
            self._set_monitor_status(False)
            return
        self._update_automatic_monitor_interval()
        self._automatic_monitor_timer.start()
        logger.info(
            "Monitorización automática activada; intervalo %s",
            self.monitor_interval_combo.currentText(),
        )
        self._set_monitor_status(True)
        self._run_automatic_monitor()

    def _update_automatic_monitor_interval(self, _text: str = "") -> None:
        intervals = {
            "10 segundos": 10_000,
            "30 segundos": 30_000,
            "60 segundos": 60_000,
            "5 minutos": 300_000,
        }
        if self.automatic_monitor_check.isChecked():
            self._automatic_monitor_timer.setInterval(
                intervals.get(self.monitor_interval_combo.currentText(), 30_000)
            )
            self._set_monitor_status(True)
            if not self._automatic_monitor_timer.isActive():
                self._automatic_monitor_timer.start()

    def _set_monitor_status(self, active: bool) -> None:
        if active and self._is_connected and not self._initializing_at:
            self.monitor_status_label.setText(
                f"Monitorización activa · {self.monitor_interval_combo.currentText()}"
            )
            self.monitor_status_label.setObjectName("connectedStatus")
        else:
            self.monitor_status_label.setText("Monitorización detenida")
            self.monitor_status_label.setObjectName("disconnectedStatus")
        self.monitor_status_label.style().unpolish(self.monitor_status_label)
        self.monitor_status_label.style().polish(self.monitor_status_label)

    def _run_automatic_monitor(self) -> None:
        if (
            not self.automatic_monitor_check.isChecked()
            or not self._is_connected
            or self._command_queue.paused
            or self._command_queue.is_busy
            or self._auto_monitor_running
        ):
            return
        timeout_ms = self.command_timeout_spin.value() * 1000
        commands = [
            make_at_command(
                encode_at_commands(SIM7080G_COMMANDS[key], "CR")[0],
                timeout_ms,
                purpose=f"auto:{key}",
            )
            for key in MONITOR_KEYS
        ]
        self._auto_monitor_running = True
        self._auto_monitor_remaining = len(commands)
        self._auto_monitor_failed = False
        first_command = self._command_queue.enqueue(commands)
        if first_command is not None:
            self._write_queued_command(first_command.data)

    def _add_event(self, direction: str, payload: bytes) -> None:
        timestamp = datetime.now()
        self._records.append((direction, payload, timestamp))
        if self._paused:
            self._pending_render.append((direction, payload, timestamp))
            return
        self._append_line(self._format_record(direction, payload, timestamp), direction)

    def _format_record(self, direction: str, payload: bytes, timestamp: datetime) -> str:
        stamp = timestamp.strftime("%H:%M:%S.") + f"{timestamp.microsecond // 1000:03d}"
        show_details = self.show_details_check.isChecked()
        if direction == "ERROR":
            content = payload.decode("utf-8", errors="replace")
        elif direction == "SYSTEM":
            content = payload.decode("utf-8", errors="replace")
        elif self.display_combo.currentText() == "HEX":
            content = payload.hex(" ").upper()
        else:
            content = self._visible_ascii(payload, show_special_characters=show_details)
        marker = {"TX": "→", "RX": "←"}.get(direction, "·")
        size = f" ({len(payload)} bytes)" if show_details and direction in {"TX", "RX"} else ""
        return f"[{stamp}] {direction} {marker} {content}{size}"

    @staticmethod
    def _visible_ascii(data: bytes, show_special_characters: bool = True) -> str:
        visible: list[str] = []
        for value in data:
            if 0x20 <= value <= 0x7E:
                visible.append(chr(value))
            elif show_special_characters and value < 0x20:
                visible.append(chr(0x2400 + value))
            elif show_special_characters and value == 0x7F:
                visible.append("␡")
            elif show_special_characters:
                visible.append(f"\\x{value:02X}")
        return "".join(visible)

    def _append_line(self, line: str, direction: str) -> None:
        cursor = self.console.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(line + "\n", self._format_for_color(direction))
        if self.autoscroll_check.isChecked():
            self.console.setTextCursor(cursor)
            self.console.ensureCursorVisible()

    def _format_for_color(self, direction: str) -> Any:
        from PySide6.QtGui import QTextCharFormat

        text_format = QTextCharFormat()
        text_format.setForeground(self._COLORS[direction])
        return text_format

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setText("Reanudar visualización" if self._paused else "Pausar visualización")
        if not self._paused:
            pending, self._pending_render = self._pending_render, []
            for direction, payload, timestamp in pending:
                self._append_line(self._format_record(direction, payload, timestamp), direction)

    def _clear_console(self) -> None:
        self.console.clear()
        self._records.clear()
        self._pending_render.clear()
        self._rx_flush_timer.stop()
        self._rx_line_buffer.clear()

    def _copy_console(self) -> None:
        self.console.selectAll()
        self.console.copy()
        self.console.moveCursor(QTextCursor.MoveOperation.End)

    def _save_session(self) -> None:
        default_path = Path.home() / "tdserial-session.txt"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Guardar sesión", str(default_path), "Archivos de texto (*.txt);;Todos los archivos (*)"
        )
        if not filename:
            return
        try:
            lines = [
                self._format_record(direction, payload, timestamp)
                for direction, payload, timestamp in self._records
            ]
            Path(filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.exception("No se pudo guardar la sesión en %s", filename)
            QMessageBox.critical(self, "Error al guardar", f"No se pudo guardar la sesión.\n{exc}")

    def _redraw_console(self) -> None:
        self.console.clear()
        visible_count = len(self._records) - (len(self._pending_render) if self._paused else 0)
        for direction, payload, timestamp in self._records[:visible_count]:
            self._append_line(self._format_record(direction, payload, timestamp), direction)

    def _persist_iccid_to_secure_store(self) -> None:
        """Save the device ICCID to secure credential manager instead of config file."""
        iccid = self.onence_iccid_edit.text().strip()
        if not iccid:
            # If ICCID is empty, delete it from credential manager
            try:
                delete_device_iccid()
            except CredentialStoreError as e:
                logger.warning("No se pudo eliminar el ICCID del almacén seguro: %s", e)
        else:
            # Store the ICCID in the secure credential manager
            try:
                store_device_iccid(iccid)
            except (ValueError, CredentialStoreError) as e:
                logger.warning("No se pudo guardar el ICCID en el almacén seguro: %s", e)
                QMessageBox.warning(
                    self,
                    "Almacén seguro",
                    f"No se pudo guardar el ICCID en el almacén seguro de Windows.\n{e}"
                )

    def _persist_config(self) -> None:
        settings = self._serial_settings()
        self._config.update(
            {
                "port": settings["port"],
                "baudrate": settings["baudrate"],
                "bytesize": settings["bytesize"],
                "parity": settings["parity"],
                "stopbits": settings["stopbits"],
                "flow_control": settings["flow_control"],
                "initialize_at_on_connect": self.initialize_at_check.isChecked(),
                "autoscroll": self.autoscroll_check.isChecked(),
                "display_format": self.display_combo.currentText(),
                "show_special_characters_and_size": self.show_details_check.isChecked(),
                "command_timeout_seconds": self.command_timeout_spin.value(),
                "command_error_policy": self.error_policy_combo.currentText(),
                "monitor_interval": self.monitor_interval_combo.currentText(),
                "signal_quality_thresholds": {
                    technology: {
                        metric: list(boundaries)
                        for metric, boundaries in metrics.items()
                    }
                    for technology, metrics in self._signal_quality_thresholds.items()
                },
                "iot_host": self.iot_host_edit.text().strip(),
                "iot_port": self.iot_port_spin.value(),
                "iot_apn": self.iot_apn_edit.text().strip(),
                "iot_context_id": self.iot_context_spin.value(),
                "iot_socket_id": self.iot_socket_spin.value(),
                "iot_payload_format": self.iot_format_combo.currentText(),
                "iot_read_length": self.iot_read_length_spin.value(),
                "iot_prompt_timeout_ms": self.iot_prompt_timeout_spin.value(),
                "iot_input_time_ms": self.iot_input_time_spin.value(),
                "iot_response_timeout_ms": self.iot_response_timeout_spin.value(),
            }
        )
        save_config(self._config)

    def closeEvent(self, event: Any) -> None:
        if self.controller_panel is not None and self.controller_panel.thread is not None:
            QMessageBox.warning(
                self, "Device Controller", "Espera a que termine la operación HTTP antes de cerrar."
            )
            event.ignore()
            return
        self._command_timeout_timer.stop()
        self._initialization_timer.stop()
        self._automatic_monitor_timer.stop()
        self._cloud_auto_timer.stop()
        if self._onence_auth_thread is not None:
            self._onence_auth_thread.wait(13_000)
        if self._onence_history_thread is not None:
            self._onence_history_thread.wait(13_000)
        try:
            self._persist_config()
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Configuración", f"No se pudo guardar la configuración local.\n{exc}")
        self.stop_requested.emit()
        self._thread.wait(3000)
        event.accept()
