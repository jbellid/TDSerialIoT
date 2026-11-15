"""Windows serial opening that preserves input queued by the driver."""

from __future__ import annotations

import ctypes

import serial
from serial import win32
from serial.serialwin32 import Serial as WindowsSerial


class SerialWithoutInputPurge(WindowsSerial):
    """Keep the Windows receive queue when opening a pySerial port."""

    def open(self) -> None:
        if self.port is None:
            raise serial.SerialException("Port must be configured before opening")
        if self.is_open:
            raise serial.SerialException("Port is already open")

        name = self.port
        if name.upper().startswith("COM") and name[3:].isdigit():
            name = "\\\\.\\" + name
        handle = win32.CreateFile(
            name,
            win32.GENERIC_READ | win32.GENERIC_WRITE,
            0,
            None,
            win32.OPEN_EXISTING,
            win32.FILE_ATTRIBUTE_NORMAL | win32.FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle == win32.INVALID_HANDLE_VALUE:
            raise serial.SerialException(f"Cannot open {self.port}: {ctypes.WinError()}")

        self._port_handle = handle
        original_timeouts = win32.COMMTIMEOUTS()
        timeouts_saved = False
        try:
            if not win32.GetCommTimeouts(handle, ctypes.byref(original_timeouts)):
                raise serial.SerialException(f"Cannot read serial timeouts: {ctypes.WinError()}")
            self._orgTimeouts = original_timeouts
            timeouts_saved = True
            self._overlapped_read = win32.OVERLAPPED()
            self._overlapped_read.hEvent = win32.CreateEvent(None, True, False, None)
            if not self._overlapped_read.hEvent:
                raise serial.SerialException(f"Cannot create read event: {ctypes.WinError()}")
            self._overlapped_write = win32.OVERLAPPED()
            self._overlapped_write.hEvent = win32.CreateEvent(None, False, False, None)
            if not self._overlapped_write.hEvent:
                raise serial.SerialException(f"Cannot create write event: {ctypes.WinError()}")
            # pySerial owns all subsequent configuration, reads, writes and close.
            self._reconfigure_port()
        except BaseException:
            if timeouts_saved:
                win32.SetCommTimeouts(handle, ctypes.byref(original_timeouts))
            for operation in (self._overlapped_read, self._overlapped_write):
                if operation is not None and operation.hEvent:
                    win32.CloseHandle(operation.hEvent)
            win32.CloseHandle(handle)
            self._port_handle = None
            self._overlapped_read = None
            self._overlapped_write = None
            raise
        self.is_open = True
