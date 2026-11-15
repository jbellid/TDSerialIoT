"""Test buffer reading functionality in SerialWorker."""

import sys
from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtWidgets import QApplication

# Ensure app exists for PySide6
app = QApplication.instance() or QApplication(sys.argv)


def test_buffer_read_signal_emitted():
    """Test that buffer_read signal is emitted when data is present."""
    from tdserial_iot.serial_worker import SerialWorker

    worker = SerialWorker()
    
    # Setup mock serial port
    mock_port = MagicMock()
    mock_port.is_open = True
    mock_port.in_waiting = 10
    mock_port.read.return_value = b"AT+CSQ\r\n"
    
    # Track signal emissions
    buffer_read_data = []
    worker.buffer_read.connect(lambda data: buffer_read_data.append(data))
    worker.connection_changed.connect(lambda x: None)
    
    with patch('tdserial_iot.serial_worker._create_serial') as MockSerial:
        MockSerial.return_value = mock_port
        
        settings = {
            'port': 'COM3',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1.0,
            'clear_buffer': False,
        }
        
        worker.open_port(settings)
    
    # Verify buffer_read was emitted
    assert len(buffer_read_data) == 1
    assert buffer_read_data[0] == b"AT+CSQ\r\n"


def test_buffer_not_read_when_empty():
    """Test that buffer_read signal is NOT emitted when buffer is empty."""
    from tdserial_iot.serial_worker import SerialWorker

    worker = SerialWorker()
    
    # Setup mock serial port with empty buffer
    mock_port = MagicMock()
    mock_port.is_open = True
    mock_port.in_waiting = 0  # Empty buffer
    
    # Track signal emissions
    buffer_read_data = []
    worker.buffer_read.connect(lambda data: buffer_read_data.append(data))
    worker.connection_changed.connect(lambda x: None)
    
    with patch('tdserial_iot.serial_worker._create_serial') as MockSerial:
        MockSerial.return_value = mock_port
        
        settings = {
            'port': 'COM3',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1.0,
            'clear_buffer': False,
        }
        
        worker.open_port(settings)
    
    # Verify buffer_read was NOT emitted
    assert len(buffer_read_data) == 0


def test_buffer_cleared_when_option_enabled():
    """Test that reset_input_buffer is called when clear_buffer is True."""
    from tdserial_iot.serial_worker import SerialWorker

    worker = SerialWorker()
    
    # Setup mock serial port
    mock_port = MagicMock()
    mock_port.is_open = True
    mock_port.in_waiting = 5
    mock_port.read.return_value = b"DATA"
    mock_port.reset_input_buffer = MagicMock()
    
    worker.connection_changed.connect(lambda x: None)
    buffer_read_data = []
    worker.buffer_read.connect(buffer_read_data.append)
    
    with patch('tdserial_iot.serial_worker._create_serial') as MockSerial:
        MockSerial.return_value = mock_port
        
        settings = {
            'port': 'COM3',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1.0,
            'clear_buffer': True,  # Enable clearing
        }
        
        worker.open_port(settings)
    
    # Verify reset_input_buffer was called
    mock_port.reset_input_buffer.assert_called_once()
    mock_port.read.assert_not_called()
    assert buffer_read_data == []


@pytest.mark.parametrize("operation", ["read", "reset_input_buffer"])
def test_buffer_operation_failure_closes_port_and_reports_error(operation):
    from serial import SerialException
    from tdserial_iot.serial_worker import SerialWorker

    worker = SerialWorker()
    mock_port = MagicMock()
    mock_port.is_open = True
    mock_port.in_waiting = 5
    getattr(mock_port, operation).side_effect = SerialException("USB disconnected")
    statuses = []
    errors = []
    worker.connection_changed.connect(statuses.append)
    worker.error_occurred.connect(errors.append)
    settings = {
        "port": "COM3",
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1.0,
        "clear_buffer": operation == "reset_input_buffer",
    }

    with patch("tdserial_iot.serial_worker._create_serial", return_value=mock_port):
        worker.open_port(settings)

    assert statuses == [False]
    assert errors and "USB disconnected" in errors[0]
    mock_port.close.assert_called_once()


def test_buffer_not_cleared_when_option_disabled():
    """Test that reset_input_buffer is NOT called when clear_buffer is False."""
    from tdserial_iot.serial_worker import SerialWorker

    worker = SerialWorker()
    
    # Setup mock serial port
    mock_port = MagicMock()
    mock_port.is_open = True
    mock_port.in_waiting = 5
    mock_port.read.return_value = b"DATA"
    mock_port.reset_input_buffer = MagicMock()
    
    worker.connection_changed.connect(lambda x: None)
    worker.buffer_read.connect(lambda x: None)
    
    with patch('tdserial_iot.serial_worker._create_serial') as MockSerial:
        MockSerial.return_value = mock_port
        
        settings = {
            'port': 'COM3',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1.0,
            'clear_buffer': False,  # Disable clearing
        }
        
        worker.open_port(settings)
    
    # Verify reset_input_buffer was NOT called
    mock_port.reset_input_buffer.assert_not_called()


def test_large_buffer_read():
    """Test reading a large buffer with multiple commands."""
    from tdserial_iot.serial_worker import SerialWorker

    worker = SerialWorker()
    
    # Simulate multiple buffered responses
    large_data = b"AT\r\nOK\r\nAT+CSQ\r\n+CSQ: 20,99\r\nOK\r\n"
    
    mock_port = MagicMock()
    mock_port.is_open = True
    mock_port.in_waiting = len(large_data)
    mock_port.read.return_value = large_data
    
    buffer_read_data = []
    worker.buffer_read.connect(lambda data: buffer_read_data.append(data))
    worker.connection_changed.connect(lambda x: None)
    
    with patch('tdserial_iot.serial_worker._create_serial') as MockSerial:
        MockSerial.return_value = mock_port
        
        settings = {
            'port': 'COM3',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1.0,
            'clear_buffer': False,
        }
        
        worker.open_port(settings)
    
    # Verify entire buffer was read and emitted
    assert len(buffer_read_data) == 1
    assert buffer_read_data[0] == large_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
