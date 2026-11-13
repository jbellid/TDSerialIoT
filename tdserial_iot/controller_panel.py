"""Remote 1NCE Controller interface."""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import keyring
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tdserial_iot.controller_core import COMMANDS, MAX_ID, ControllerClient, ControllerError, ControllerStore, DEFAULT_BASE_URL, build_payload
from tdserial_iot.onence_api import ApiCredentials, CredentialStoreError, store_api_credentials

KEYRING_SERVICE = "TdSerial IoT - Device Controller"
logger = logging.getLogger(__name__)


def _display_history_date(value: str) -> str:
    if not value:
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Fecha no válida en el historial de Device Controller: %s", value)
        return value
    if parsed.utcoffset() is None:
        return value
    return re.sub(r"(?:Z|[+-]\d{2}(?::?\d{2})?(?::?\d{2}(?:\.\d+)?)?)$", "", value)


def _display_history_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    values = list(row[1:])
    values[0] = _display_history_date(values[0])
    values[8] = values[8].removesuffix(" (fecha sin zona)")
    values[9] = _display_history_date(values[9])
    return tuple(values)


class ControllerWorker(QObject):
    completed = Signal(str, bool, object)

    def __init__(self, operation: str, work: Callable[[], Any]) -> None:
        super().__init__()
        self.operation, self.work = operation, work

    @Slot()
    def run(self) -> None:
        try:
            result = self.work()
        except (ControllerError, ValueError) as exc:
            self.completed.emit(self.operation, False, str(exc))
        except Exception as exc:
            logger.error("Error inesperado en Device Controller (%s)", type(exc).__name__)
            self.completed.emit(self.operation, False, "Error inesperado en la operación HTTP; revisa el log técnico.")
        else:
            self.completed.emit(self.operation, True, result)


class ControllerPanel(QWidget):
    refresh_devices_requested = Signal()

    def __init__(self, credentials: Callable[[], ApiCredentials], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.credentials = credentials
        self.store = ControllerStore()
        self.client: ControllerClient | None = None
        self.client_key: tuple[str, str, str, int] | None = None
        self.thread: QThread | None = None
        self.worker: ControllerWorker | None = None
        self.pending_record: int | None = None
        self.pending_action_id = ""
        self.command = "led_on"
        self.command_id: int | None = None
        self._build()
        self._refresh_devices()
        self._refresh_history()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        config = QGroupBox("Conexión 1NCE")
        form = QFormLayout(config)
        self.base = QLineEdit(self.store.get_setting("base_url", DEFAULT_BASE_URL))
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setValue(int(self.store.get_setting("timeout", "30")))
        self.mode = QComboBox()
        self.mode.addItems(["SEND_NOW", "SEND_WHEN_ACTIVE"])
        self.mode.setCurrentText(self.store.get_setting("mode", "SEND_NOW"))
        form.addRow("API Base URL", self.base)
        form.addRow("Timeout HTTP (s)", self.timeout)
        form.addRow("Modo de envío", self.mode)
        form.addRow(QLabel("Las credenciales se toman de la pestaña 1NCE Cloud."))
        buttons = QHBoxLayout()
        for text, action in (
            ("Guardar configuración", self._save_config),
            ("Probar autenticación", self._test_auth),
            ("Cerrar sesión", self._logout),
        ):
            button = QPushButton(text)
            button.clicked.connect(action)
            buttons.addWidget(button)
        form.addRow(buttons)
        self.auth_status = QLabel("Sin autenticar")
        form.addRow("Estado", self.auth_status)
        layout.addWidget(config)

        device = QGroupBox("Dispositivo remoto")
        form = QFormLayout(device)
        self.devices = QComboBox()
        self.devices.currentIndexChanged.connect(self._select_device)
        self.iccid, self.name, self.model, self.notes = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.cloud_devices = QComboBox()
        self.cloud_devices.addItem("Selecciona un dispositivo de 1NCE", "")
        self.cloud_devices.currentIndexChanged.connect(self._select_cloud_device)
        self.refresh_devices_button = QPushButton("Actualizar dispositivos")
        self.refresh_devices_button.clicked.connect(self.refresh_devices_requested.emit)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(3000)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Seleccionar", self.devices)
        iccid_row = QHBoxLayout()
        iccid_row.addWidget(self.iccid)
        iccid_row.addWidget(self.cloud_devices)
        iccid_row.addWidget(self.refresh_devices_button)
        form.addRow("ICCID / Device ID", iccid_row)
        form.addRow("Nombre", self.name)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model)
        model_row.addWidget(QLabel("Observaciones"))
        model_row.addWidget(self.notes)
        form.addRow("Modelo", model_row)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token)
        token_row.addWidget(QLabel("Puerto UDP"))
        token_row.addWidget(self.port)
        form.addRow("Token del firmware", token_row)
        buttons = QHBoxLayout()
        for text, action in (("Nuevo", self._new_device), ("Guardar dispositivo", self._save_device), ("Eliminar", self._delete_device)):
            button = QPushButton(text)
            button.clicked.connect(action)
            buttons.addWidget(button)
        form.addRow(buttons)
        layout.addWidget(device)

        commands = QGroupBox("Comandos remotos")
        command_layout = QVBoxLayout(commands)
        buttons = QHBoxLayout()
        for text, command in (
            ("Encender LED", "led_on"), ("Apagar LED", "led_off"), ("Alternar LED", "led_toggle"),
            ("Buzzer", "buzzer"), ("Telemetría", "telemetry"), ("GPS", "gps"),
            ("Estado", "status"), ("Reiniciar", "reboot"),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, value=command: self._choose_command(value))
            buttons.addWidget(button)
        command_layout.addLayout(buttons)
        command_layout.addWidget(QLabel("Buzzer: 500 ms. GNSS puede interrumpir temporalmente la conectividad celular."))
        self.edit_command_check = QCheckBox("Editar comando")
        self.edit_command_check.setObjectName("toolbarCheck")
        self.edit_command_check.toggled.connect(self._set_command_editable)
        command_layout.addWidget(self.edit_command_check)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(145)
        command_layout.addWidget(self.preview)
        self.send = QPushButton("Enviar comando")
        self.send.clicked.connect(self._send)
        command_layout.addWidget(self.send)
        layout.addWidget(commands)

        history_group = QGroupBox("Historial local")
        history_layout = QVBoxLayout(history_group)
        self.history_table = QTableWidget(0, 9)
        self.history_table.setMinimumHeight(200)
        self.history_table.setHorizontalHeaderLabels(
            ["Fecha", "Dispositivo", "Comando", "ID comando", "ID acción", "Modo", "Estado API", "Ejecución", "Fecha ACK"]
        )
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.cellClicked.connect(self._select_history_action)
        history_layout.addWidget(self.history_table)
        self.action_id = QLineEdit()
        self.action_id.setPlaceholderText("ID de acción 1NCE")
        history_layout.addWidget(self.action_id)
        buttons = QHBoxLayout()
        for text, action in (("Consultar estado", self._status), ("Copiar historial", self._copy), ("Exportar CSV", self._export)):
            button = QPushButton(text)
            button.clicked.connect(action)
            buttons.addWidget(button)
        history_layout.addLayout(buttons)
        layout.addWidget(history_group)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(110)
        layout.addWidget(self.console)

    def _log(self, message: str) -> None:
        self.console.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}")

    def _show_error(self, message: str) -> None:
        self._log("ERROR: " + message)
        QMessageBox.warning(self, "Device Controller", message)

    def _save_config(self) -> None:
        try:
            credentials = self.credentials()
            client = ControllerClient(self.base.text(), credentials.client_id, credentials.client_secret, self.timeout.value())
            store_api_credentials(credentials)
            for key, value in (("base_url", client.base_url), ("timeout", str(self.timeout.value())), ("mode", self.mode.currentText())):
                self.store.set_setting(key, value)
        except (ValueError, CredentialStoreError, OSError, sqlite3.Error) as exc:
            self._show_error(str(exc))
            return
        self._log("Configuración guardada; credenciales en el almacén seguro.")

    def _get_client(self) -> ControllerClient:
        credentials = self.credentials()
        key = (self.base.text(), credentials.client_id, credentials.client_secret, self.timeout.value())
        if self.client is None or self.client_key != key:
            self.client = ControllerClient(*key)
            self.client_key = key
        return self.client

    def _logout(self) -> None:
        if self.thread is not None:
            self._show_error("Espera a que termine la operación en curso.")
            return
        self.client = None
        self.client_key = None
        self.auth_status.setText("Sesión local cerrada")

    def _run(self, operation: str, work: Callable[[], Any]) -> None:
        if self.thread is not None:
            raise ValueError("Ya hay una operación en curso.")
        thread = QThread(self)
        worker = ControllerWorker(operation, work)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._completed)
        worker.completed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.thread, self.worker = thread, worker
        self.send.setEnabled(False)
        thread.start()

    @Slot()
    def _thread_finished(self) -> None:
        self.thread = None
        self.worker = None
        self.send.setEnabled(True)

    def _test_auth(self) -> None:
        try:
            client = self._get_client()
            self._run("auth", lambda: client.authenticate(force=True))
            self._log("API → Autenticando con 1NCE")
        except ValueError as exc:
            self._show_error(str(exc))

    @Slot(str, bool, object)
    def _completed(self, operation: str, success: bool, result: object) -> None:
        if not success:
            if operation == "send" and self.pending_record is not None:
                self.store.update_record(self.pending_record, "Error o resultado desconocido")
                self.pending_record = None
                self._refresh_history()
            self.auth_status.setText("Error de operación")
            self._show_error(str(result))
            return
        if operation == "auth":
            self.auth_status.setText("Autenticación correcta")
            self._log("API ← Autenticación correcta")
        elif operation == "send":
            assert isinstance(result, str)
            if self.pending_record is not None:
                self.store.update_record(self.pending_record, "HTTP 202 aceptado", result)
                self.pending_record = None
            self.action_id.setText(result)
            self._refresh_history()
            self._log("API ← Solicitud aceptada por 1NCE OS. Ejecución sin confirmar.")
        else:
            assert isinstance(result, dict)
            raw_status = result.get("status")
            status = (
                raw_status
                if isinstance(raw_status, str)
                and raw_status in {"SCHEDULED", "IN_PROGRESS", "SUCCEEDED", "FAILED", "CANCELLED"}
                else "Estado desconocido"
            )
            self._log(
                f"API ← Acción {self.pending_action_id}: {status}. UDP no confirma ejecución."
            )
            device_id = result.get("deviceId")
            if isinstance(device_id, str) and device_id.isascii() and device_id.isdigit() and 18 <= len(device_id) <= 22:
                self._log(f"API ← Dispositivo: {device_id}")
            for row in self.store.history():
                if row[6] == self.pending_action_id:
                    self.store.update_record(row[0], str(status))
                    self._log(f"API ← Comando local: {row[4]} · {row[1]}")
                    break
            self.pending_action_id = ""
            self._refresh_history()

    def _refresh_devices(self) -> None:
        selected = self.devices.currentData() or self.store.get_setting("active_device", "")
        self.devices.blockSignals(True)
        self.devices.clear()
        for iccid, name, *_ in self.store.devices():
            self.devices.addItem(f"{name} · {iccid}", iccid)
        index = self.devices.findData(selected)
        if index >= 0:
            self.devices.setCurrentIndex(index)
        self.devices.blockSignals(False)
        self._select_device()

    def set_cloud_devices(self, devices: list[tuple[str, str]]) -> None:
        selected = self.iccid.text().strip()
        self.cloud_devices.blockSignals(True)
        self.cloud_devices.clear()
        self.cloud_devices.addItem("Selecciona un dispositivo de 1NCE", "")
        for iccid, label in devices:
            self.cloud_devices.addItem(f"{label} · {iccid}" if label else iccid, iccid)
        index = self.cloud_devices.findData(selected)
        self.cloud_devices.setCurrentIndex(max(index, 0))
        self.cloud_devices.blockSignals(False)

    def _select_cloud_device(self) -> None:
        iccid = self.cloud_devices.currentData()
        if not iccid:
            return
        local_index = self.devices.findData(iccid)
        if local_index >= 0:
            if self.devices.currentIndex() == local_index:
                self._select_device()
            else:
                self.devices.setCurrentIndex(local_index)
        else:
            self._new_device()
            self.iccid.setText(str(iccid))
            label = self.cloud_devices.currentText().removesuffix(f" · {iccid}")
            self.name.setText(label if label != iccid else "")
            self._log("Dispositivo de 1NCE seleccionado; guarda sus datos y token para enviar.")

    def _select_device(self) -> None:
        selected = self.devices.currentData()
        self.token.clear()
        if selected:
            self.store.set_setting("active_device", str(selected))
        for iccid, name, model, port, notes, _next_id in self.store.devices():
            if iccid == selected:
                self.iccid.setText(iccid)
                self.name.setText(name)
                self.model.setText(model)
                self.port.setValue(port)
                self.notes.setText(notes)
                try:
                    self.token.setText(keyring.get_password(KEYRING_SERVICE, iccid) or "")
                except keyring.errors.KeyringError as exc:
                    self._show_error(f"No se pudo leer el token del almacén seguro: {type(exc).__name__}")
                break
        self.command_id = None
        self._preview(force=True)

    def _new_device(self) -> None:
        for edit in (self.iccid, self.name, self.model, self.notes, self.token):
            edit.clear()
        self.port.setValue(3000)
        self.command_id = None
        self._preview(force=True)

    def _save_device(self) -> None:
        iccid = self.iccid.text().strip()
        try:
            build_payload("led_on", 1, self.token.text())
            self.store.save_device(iccid, self.name.text(), self.model.text(), self.port.value(), self.notes.text())
            keyring.set_password(KEYRING_SERVICE, iccid, self.token.text())
        except ValueError as exc:
            self._show_error(str(exc))
            return
        except (OSError, sqlite3.Error, keyring.errors.KeyringError) as exc:
            self._show_error(f"No se pudo guardar el dispositivo: {type(exc).__name__}")
            return
        self._refresh_devices()
        self.devices.setCurrentIndex(self.devices.findData(iccid))
        self._log("Dispositivo guardado.")

    def _delete_device(self) -> None:
        iccid = self.devices.currentData()
        if iccid and QMessageBox.question(self, "Eliminar", "¿Eliminar el dispositivo seleccionado?") == QMessageBox.StandardButton.Yes:
            try:
                keyring.delete_password(KEYRING_SERVICE, str(iccid))
                self.store.delete_device(str(iccid))
            except (keyring.errors.KeyringError, sqlite3.Error) as exc:
                self._show_error(f"No se pudo eliminar el dispositivo: {type(exc).__name__}")
                return
            self._refresh_devices()

    def _choose_command(self, command: str) -> None:
        try:
            iccid = self.devices.currentData()
            if not iccid:
                raise ValueError("Selecciona un dispositivo guardado.")
            if self.iccid.text().strip() != iccid:
                raise ValueError("Guarda y selecciona el dispositivo antes de enviar el comando.")
            command_id = self.store.next_id(str(iccid))
        except (ValueError, sqlite3.Error) as exc:
            self._show_error(str(exc))
            return
        self.command = command
        self.command_id = command_id
        self._preview(force=True)

    def _set_command_editable(self, enabled: bool) -> None:
        self.preview.setReadOnly(not enabled)
        if not enabled:
            self._preview(force=True)

    def _preview(self, *, force: bool = False) -> None:
        if self.edit_command_check.isChecked() and not force:
            return
        data: dict[str, Any] = {"cmd": self.command, "id": self.command_id or "Se asignará al enviar"}
        if self.command == "buzzer":
            data["duration_ms"] = 500
        if self.command == "reboot":
            data["confirm"] = "REBOOT"
        data["token"] = "********"
        self.preview.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))

    def _send(self) -> None:
        try:
            iccid = self.devices.currentData()
            if not iccid:
                raise ValueError("Selecciona un dispositivo guardado.")
            if self.iccid.text().strip() != iccid:
                raise ValueError("Guarda y selecciona el dispositivo antes de enviar el comando.")
            try:
                data = json.loads(self.preview.toPlainText())
            except json.JSONDecodeError:
                raise ValueError("El comando debe ser un objeto JSON válido.") from None
            if not isinstance(data, dict):
                raise ValueError("El comando debe ser un objeto JSON.")
            command = data.get("cmd")
            if not isinstance(command, str) or not command:
                raise ValueError("El comando JSON debe incluir un campo cmd de texto.")
            command_id = data.get("id")
            reserved_id = self.command_id
            if command_id == "Se asignará al enviar":
                command_id = self.store.next_id(str(iccid))
                reserved_id = command_id
            if not isinstance(command_id, int) or isinstance(command_id, bool) or not 1 <= command_id <= MAX_ID:
                raise ValueError("El ID debe ser un entero positivo de 31 bits.")
            if command.casefold() == "reboot" and QMessageBox.question(
                self, "Confirmar reinicio", "¿Enviar una orden de reinicio al dispositivo?"
            ) != QMessageBox.StandardButton.Yes:
                return
            client = self._get_client()
            if self.thread is not None:
                raise ValueError("Ya hay una operación en curso.")
            if data.get("token") == "********":
                build_payload("led_on", command_id, self.token.text())
                data["token"] = self.token.text()
            data["id"] = command_id
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if len(payload.encode("utf-8")) > 400:
                raise ValueError("El payload supera el límite de 400 bytes del firmware.")
            self.store.reserve_id(str(iccid), command_id, reserved_id)
            mode = self.mode.currentText()
            port = self.port.value()
            history_command = command if command in COMMANDS else "Personalizado"
            self.pending_record = self.store.record(
                datetime.now().astimezone().isoformat(timespec="seconds"), str(iccid), self.name.text(),
                history_command, command_id, mode,
            )
            self._refresh_history()
            self._log(f"API → Enviando {history_command}; puerto {port}")
            self._run("send", lambda: client.send(str(iccid), port, mode, payload))
            self.command_id = None
            self._preview()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._show_error(str(exc))

    def _status(self) -> None:
        try:
            client = self._get_client()
            action_id = self.action_id.text().strip()
            self._run("status", lambda: client.status(action_id))
            self.pending_action_id = action_id
        except ValueError as exc:
            self._show_error(str(exc))

    def _refresh_history(self) -> None:
        rows = self.store.history()
        self.history_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = _display_history_row(row)
            for column, value in enumerate((values[0], f"{values[2]} · {values[1]}", *values[3:])):
                self.history_table.setItem(index, column, QTableWidgetItem(str(value)))
            for column, original in ((0, row[1]), (8, row[10])):
                if original:
                    self.history_table.item(index, column).setToolTip(str(original))

    @Slot(int, int)
    def _select_history_action(self, row: int, column: int) -> None:
        action = self.history_table.item(row, 4)
        self.action_id.setText(action.text() if action is not None else "")

    def _copy(self) -> None:
        rows = self.store.history()
        QApplication.clipboard().setText(
            "\n".join("\t".join(str(value) for value in _display_history_row(row)) for row in rows)
        )

    def _export(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Exportar historial", str(Path.home() / "controller.csv"), "CSV (*.csv)")
        if not filename:
            return
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(("Fecha", "ICCID", "Nombre", "Comando", "ID comando", "ID acción", "Modo", "Estado API", "Ejecución", "Fecha ACK"))
                writer.writerows(_display_history_row(row) for row in self.store.history())
        except OSError as exc:
            self._show_error(f"No se pudo exportar el historial: {exc}")
