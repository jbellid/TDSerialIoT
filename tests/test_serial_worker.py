from collections import deque

import pytest
from serial import SerialException

from tdserial_iot.serial_worker import SerialWorker


class FakeSerial:
    instances = []

    def __init__(self, **settings):
        self.settings = settings
        self.port = settings.get("port")
        self.baudrate = settings.get("baudrate")
        self.is_open = False
        self.dtr = True
        self.rts = True
        self.break_condition = True
        self.open_state = None
        self.incoming = deque()
        self.writes = []
        self.write_result = None
        type(self).instances.append(self)

    def open(self):
        self.open_state = (
            self.port,
            self.dtr,
            self.rts,
            self.break_condition,
            self.settings["xonxoff"],
            self.settings["rtscts"],
            self.settings["dsrdtr"],
        )
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.incoming[0]) if self.incoming else 0

    def read(self, size):
        part = self.incoming.popleft()
        if len(part) > size:
            self.incoming.appendleft(part[size:])
            return part[:size]
        return part

    def write(self, data):
        count = len(data) if self.write_result is None else self.write_result
        self.writes.append(data[:count])
        return count

    def close(self):
        self.is_open = False


@pytest.fixture
def worker(monkeypatch):
    FakeSerial.instances.clear()
    monkeypatch.setattr("tdserial_iot.serial_worker.serial.Serial", FakeSerial)
    instance = SerialWorker()
    tx_events = []
    rx_events = []
    status_events = []
    errors = []
    instance.transmitted.connect(tx_events.append)
    instance.received.connect(rx_events.append)
    instance.connection_changed.connect(status_events.append)
    instance.error_occurred.connect(errors.append)
    instance.open_port(
        {
            "port": "COM6",
            "baudrate": 115200,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "xonxoff": False,
            "rtscts": False,
            "dsrdtr": False,
        }
    )
    fake = FakeSerial.instances[-1]
    return instance, fake, tx_events, rx_events, status_events, errors


def test_transmit_is_emitted_from_bytes_actually_written(worker):
    instance, fake, tx, _, _, _ = worker
    fake.write_result = 2
    instance.write_bytes(b"AT\r")
    assert fake.writes == [b"AT"]
    assert tx == [b"AT"]


def test_receive_preserves_fragmented_bytes(worker):
    instance, fake, _, rx, _, _ = worker
    fake.incoming.extend([b"+CS", b"Q: 18\r\n"])
    instance._poll()
    instance._poll()
    assert rx == [b"+CS", b"Q: 18\r\n"]


def test_echo_like_data_is_still_received_data(worker):
    instance, fake, tx, rx, _, _ = worker
    instance.write_bytes(b"AT\r")
    fake.incoming.append(b"AT\r\r\n")
    instance._poll()
    assert tx == [b"AT\r"]
    assert rx == [b"AT\r\r\n"]


def test_unexpected_disconnect_reports_error_and_clears_connection(worker):
    instance, fake, _, _, statuses, errors = worker

    def fail_read(size):
        assert size > 0
        raise SerialException("USB disconnected")

    fake.incoming.append(b"x")
    fake.read = fail_read
    instance._poll()
    assert errors and "USB disconnected" in errors[0]
    assert statuses[-1] is False
    assert fake.is_open is False


def test_port_open_failure_is_reported(monkeypatch):
    class OpenFailureSerial(FakeSerial):
        def open(self):
            assert self.port == "COM6"
            raise SerialException("Access denied")

    def fail_open(**settings):
        return OpenFailureSerial(**settings)

    FakeSerial.instances.clear()
    monkeypatch.setattr("tdserial_iot.serial_worker.serial.Serial", fail_open)
    instance = SerialWorker()
    errors = []
    statuses = []
    instance.error_occurred.connect(errors.append)
    instance.connection_changed.connect(statuses.append)
    instance.open_port(
        {
            "port": "COM6",
            "baudrate": 115200,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "xonxoff": True,
            "rtscts": True,
            "dsrdtr": True,
        }
    )
    assert statuses == [False]
    assert len(errors) == 1
    assert "Access denied" in errors[0]


def test_port_is_configured_passively_before_explicit_open(worker):
    _, fake, _, _, _, _ = worker
    assert fake.settings["port"] is None
    assert fake.settings["xonxoff"] is False
    assert fake.settings["rtscts"] is False
    assert fake.settings["dsrdtr"] is False
    assert fake.open_state == ("COM6", False, False, False, False, False, False)
    assert fake.writes == []


def test_rx_lines_are_not_joined_when_serial_reads_contain_multiple_responses():
    from tdserial_iot.serial_lines import SerialLineBuffer

    line_buffer = SerialLineBuffer()
    assert line_buffer.feed(
        b"AT+CNACT?\r\n+CNACT: 0,1,\"10.0.0.1\"\r\n+CNACT: 1,0,\"0.0.0.0\"\r\n"
    ) == [
        b"AT+CNACT?\r\n",
        b"+CNACT: 0,1,\"10.0.0.1\"\r\n",
        b"+CNACT: 1,0,\"0.0.0.0\"\r\n",
    ]


def test_rx_line_buffer_reassembles_fragmented_input_and_keeps_echo_as_rx():
    from tdserial_iot.serial_lines import SerialLineBuffer

    line_buffer = SerialLineBuffer()
    assert line_buffer.feed(b"AT+CN") == []
    assert line_buffer.feed(b"ACT?\r") == []
    assert line_buffer.feed(b"\n+CNACT: 0,1\r\n") == [
        b"AT+CNACT?\r\n",
        b"+CNACT: 0,1\r\n",
    ]


def test_rx_line_buffer_flushes_unterminated_prompt_or_partial_data():
    from tdserial_iot.serial_lines import SerialLineBuffer

    line_buffer = SerialLineBuffer()
    assert line_buffer.feed(b"> ") == []
    assert line_buffer.flush_ready() == b"> "
    assert line_buffer.feed(b"OK") == []
    assert line_buffer.flush_ready() is None
    assert line_buffer.feed(b"\r") == []
    assert line_buffer.flush_ready() == b"OK\r"
    assert line_buffer.flush() is None


def test_command_queue_waits_for_terminal_result_and_ignores_urcs():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    first = queue.enqueue([make_at_command(b"ATI\r"), make_at_command(b"AT+CSQ\r")])
    assert first is not None and first.data == b"ATI\r"
    assert queue.active == b"ATI\r"
    assert queue.accept_line(b"ATI\r\n") is None
    assert queue.accept_line(b"+CREG: 1\r\n") is None
    assert queue.active == b"ATI\r"
    event = queue.accept_line(b"OK\r\n")
    assert event is not None and event.kind == "success"
    next_command = queue.start_next()
    assert next_command is not None and next_command.data == b"AT+CSQ\r"
    assert queue.active == b"AT+CSQ\r"
    assert queue.accept_line(b"+CSQ: 20,99\r\n") is None
    event = queue.accept_line(b"ERROR\r\n")
    assert event is not None and event.kind == "error"
    assert queue.active is None
    assert not queue.is_busy


def test_command_queue_timeout_pauses_without_advancing_until_resumed():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    queue.enqueue([make_at_command(b"ATI\r"), make_at_command(b"AT+CSQ\r")])
    timed_out = queue.timeout()
    assert timed_out is not None and timed_out.data == b"ATI\r"
    assert queue.paused
    assert queue.active is None
    assert queue.pending_count == 1
    assert queue.start_next() is None
    next_command = queue.resume()
    assert next_command is not None and next_command.data == b"AT+CSQ\r"


def test_late_terminal_after_timeout_does_not_advance_pending_queue():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    queue.enqueue([make_at_command(b"ATI\r"), make_at_command(b"AT+CSQ\r")])
    assert queue.timeout() is not None
    assert queue.accept_line(b"OK\r\n") is None
    assert queue.active is None
    assert queue.pending_count == 1
    assert queue.start_next() is None


def test_terminal_results_match_complete_lines_only():
    from tdserial_iot.command_queue import classify_terminal_response

    assert classify_terminal_response(b"OK\r\n") == "success"
    assert classify_terminal_response(b"ERROR\r\n") == "error"
    assert classify_terminal_response(b"+CME ERROR: 3\r\n") == "error"
    assert classify_terminal_response(b"+CMS ERROR: 500\r\n") == "error"
    assert classify_terminal_response(b"+CME ERROR extra\r\n") is None
    assert classify_terminal_response(b"prefix OK suffix\r\n") is None
    assert classify_terminal_response(b"OK") == "success"


def test_command_queue_requires_complete_line_and_ignores_echo():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    queue.enqueue([make_at_command(b"AT+CSQ\r")])
    assert queue.accept_line(b"AT+CSQ\r\n") is None
    assert queue.accept_line(b"OK", complete=False) is None
    assert queue.active == b"AT+CSQ\r"
    result = queue.accept_line(b"OK\r\n")
    assert result is not None and result.kind == "success"


def test_caopen_requires_ok_and_correlated_async_confirmation():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    queue.enqueue(
        [
            make_at_command(b'AT+CAOPEN=0,0,"UDP","example.org",4445\r'),
            make_at_command(b"AT+CSQ\r"),
        ]
    )
    assert queue.accept_line(b"+CAOPEN: 0,0\r\n") is None
    assert queue.active is not None
    result = queue.accept_line(b"OK\r\n")
    assert result is not None and result.kind == "success"
    next_command = queue.start_next()
    assert next_command is not None and next_command.data == b"AT+CSQ\r"


def test_caopen_error_confirmation_pauses_as_failure():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    queue.enqueue([make_at_command(b"AT+CAOPEN=1,0\r"), make_at_command(b"AT\r")])
    assert queue.accept_line(b"OK\r\n") is None
    result = queue.accept_line(b"+CAOPEN: 1,3\r\n")
    assert result is not None and result.kind == "error"
    queue.pause()
    assert queue.start_next() is None


def test_interactive_prompt_produces_payload_without_completing_command():
    from tdserial_iot.command_queue import AtCommand, AtCommandQueue, InteractionPlan

    queue = AtCommandQueue()
    queue.enqueue(
        [
            AtCommand(
                b"AT+CASEND=0,4\r",
                interaction=InteractionPlan(prompt=b">", payload=b"data"),
            ),
            AtCommand(b"AT\r"),
        ]
    )
    event = queue.accept_line(b"> ", complete=False)
    assert event is not None and event.kind == "prompt" and event.payload == b"data"
    assert queue.active == b"AT+CASEND=0,4\r"
    assert queue.accept_line(b"> ", complete=False) is None
    assert queue.accept_line(b"OK\r\n") is not None


def test_cancel_pending_keeps_active_command_and_prevents_next_send():
    from tdserial_iot.command_queue import AtCommandQueue, make_at_command

    queue = AtCommandQueue()
    queue.enqueue([make_at_command(b"ATI\r"), make_at_command(b"AT+CSQ\r")])
    assert queue.cancel_pending() == 1
    assert queue.active == b"ATI\r"
    assert queue.paused
    assert queue.accept_line(b"OK\r\n") is not None
    assert queue.start_next() is None
    queue.finish_cancellation()
    assert not queue.paused
    assert not queue.is_busy
