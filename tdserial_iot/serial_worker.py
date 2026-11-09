"""Single-owner serial port worker; all port reads and writes happen in its Qt thread."""

from __future__ import annotations

import logging
from typing import Any

import serial
from PySide6.QtCore import QObject, QTimer, Signal, Slot

logger = logging.getLogger(__name__)


class SerialWorker(QObject):
    received = Signal(bytes)
    transmitted = Signal(bytes)
    connection_changed = Signal(bool)
    error_occurred = Signal(str)
    stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._port: serial.Serial | None = None
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    @Slot(dict)
    def open_port(self, settings: dict[str, Any]) -> None:
        if self._port is not None and self._port.is_open:
            self._close_port()
        port_name = settings["port"]
        logger.info(
            "Abriendo puerto serie %s; DTR=False RTS=False; flow control desactivado",
            port_name,
        )
        try:
            port = serial.Serial(
                port=None,
                baudrate=int(settings["baudrate"]),
                bytesize=int(settings["bytesize"]),
                parity=settings["parity"],
                stopbits=float(settings["stopbits"]),
                timeout=0,
                write_timeout=1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            port.dtr = False
            port.rts = False
            port.break_condition = False
            port.port = port_name
            self._log_line_states("antes de abrir", port)
            port.open()
        except (serial.SerialException, OSError, ValueError, TypeError) as exc:
            logger.exception("No se pudo abrir el puerto serie %s", port_name)
            self.error_occurred.emit(
                f"No se pudo abrir {port_name or 'el puerto'}.\n"
                f"Comprueba que existe, que no está ocupado y que la configuración es válida.\n"
                f"Detalle técnico: {exc}"
            )
            self.connection_changed.emit(False)
            return

        self._port = port
        self._log_line_states("después de abrir", port)
        logger.info("Puerto serie abierto: %s (%s baudios)", port.port, port.baudrate)
        self.connection_changed.emit(True)

    @Slot(bytes)
    def write_bytes(self, data: bytes) -> None:
        port = self._port
        if port is None or not port.is_open:
            self.error_occurred.emit("No hay ningún puerto serie conectado.")
            return
        try:
            written = port.write(data)
            if written:
                actual = data[:written]
                logger.info("TX %d bytes: %s", written, actual.hex(" "))
                self.transmitted.emit(actual)
            if written != len(data):
                logger.warning("Escritura serie parcial: %d de %d bytes", written, len(data))
                self.error_occurred.emit(
                    f"Escritura incompleta: se enviaron {written} de {len(data)} bytes."
                )
        except (serial.SerialException, OSError) as exc:
            self._handle_port_error("Error al escribir en el puerto serie", exc)

    @Slot()
    def _poll(self) -> None:
        port = self._port
        if port is None or not port.is_open:
            return
        try:
            available = port.in_waiting
            if available:
                data = port.read(available)
                if data:
                    logger.info("RX %d bytes: %s", len(data), data.hex(" "))
                    self.received.emit(data)
        except (serial.SerialException, OSError) as exc:
            self._handle_port_error("Error al leer del puerto serie", exc)

    @Slot()
    def close_port(self) -> None:
        self._close_port()

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._close_port()
        self.stopped.emit()

    def _close_port(self) -> None:
        port, self._port = self._port, None
        if port is not None:
            self._log_line_states("antes de cerrar", port)
            try:
                if port.is_open:
                    port.close()
            except (serial.SerialException, OSError) as exc:
                logger.exception("Error al cerrar el puerto serie")
                self.error_occurred.emit(f"Error al cerrar el puerto. Detalle técnico: {exc}")
        else:
            logger.info("Cierre de puerto serie solicitado sin puerto abierto")
        logger.info("Puerto serie desconectado")
        self.connection_changed.emit(False)

    @staticmethod
    def _log_line_states(stage: str, port: serial.Serial) -> None:
        try:
            logger.info(
                "Puerto %s %s: DTR solicitado=False actual=%s; "
                "RTS solicitado=False actual=%s; BREAK=%s",
                port.port,
                stage,
                port.dtr,
                port.rts,
                port.break_condition,
            )
        except (serial.SerialException, OSError, AttributeError) as exc:
            logger.warning(
                "No se pudieron consultar DTR/RTS/BREAK del puerto %s %s: %s",
                port.port,
                stage,
                exc,
            )

    def _handle_port_error(self, message: str, exc: Exception) -> None:
        logger.exception("%s", message)
        self.error_occurred.emit(f"{message}.\nDetalle técnico: {exc}")
        self._close_port()
