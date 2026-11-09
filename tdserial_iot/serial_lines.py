"""Split serial byte streams into CR/LF-delimited records."""

from __future__ import annotations

import re

_LINE_END = re.compile(b"\r\n|\r|\n")
_CARECV_HEADER_LENGTH = re.compile(rb"^\+CARECV:\s*(\d+)(?:\s*,|[\r\n])")


class CarecvFrameBuffer:
    """Extract the length-framed payload from a SIM7080G CARECV response."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._payload_remaining: int | None = None
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def begin(self) -> None:
        self._pending.clear()
        self._payload_remaining = None
        self._enabled = True

    def feed(self, data: bytes) -> list[tuple[str, bytes]]:
        if not self._enabled:
            return [("stream", data)] if data else []
        self._pending.extend(data)
        chunks: list[tuple[str, bytes]] = []

        while True:
            if self._payload_remaining is not None:
                available = min(len(self._pending), self._payload_remaining)
                if not available:
                    break
                chunks.append(("payload", bytes(self._pending[:available])))
                del self._pending[:available]
                self._payload_remaining -= available
                if self._payload_remaining:
                    break
                self._payload_remaining = None
                continue

            match = _LINE_END.search(self._pending)
            if match is None:
                break
            if match.group() == b"\r" and match.end() == len(self._pending):
                break
            line_end = match.end()
            line = bytes(self._pending[:line_end])
            del self._pending[:line_end]
            chunks.append(("stream", line))
            header = _CARECV_HEADER_LENGTH.match(line)
            if header is not None:
                payload_length = int(header.group(1))
                if payload_length:
                    self._payload_remaining = payload_length

        return chunks

    def finish(self) -> list[tuple[str, bytes]]:
        """Return any incomplete frame bytes as raw data and disable framing."""
        chunks: list[tuple[str, bytes]] = []
        if self._pending:
            kind = "payload" if self._payload_remaining is not None else "stream"
            chunks.append((kind, bytes(self._pending)))
        self._pending.clear()
        self._payload_remaining = None
        self._enabled = False
        return chunks


class SerialLineBuffer:
    """Buffer fragmented serial reads until a complete line is available."""

    def __init__(self) -> None:
        self._pending = bytearray()

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def feed(self, data: bytes) -> list[bytes]:
        self._pending.extend(data)
        lines: list[bytes] = []
        offset = 0
        while match := _LINE_END.search(self._pending, offset):
            if match.group() == b"\r" and match.end() == len(self._pending):
                break
            line = bytes(self._pending[offset : match.end()])
            if line.strip(b"\r\n"):
                lines.append(line)
            offset = match.end()

        if offset:
            del self._pending[:offset]
        return lines

    def flush(self) -> bytes | None:
        if not self._pending:
            return None
        line = bytes(self._pending)
        self._pending.clear()
        return line

    def flush_ready(self) -> bytes | None:
        """Flush complete CR/LF lines or an unterminated modem prompt only."""
        if self._pending.endswith((b"\r", b"\n")):
            return self.flush()
        if bytes(self._pending).strip(b" \t") == b">":
            return self.flush()
        return None

    def flush_prompt(self) -> bytes | None:
        """Return a prompt as soon as its bytes arrive, without waiting for CR/LF."""
        if bytes(self._pending).strip(b" \t") == b">":
            return self.flush()
        return None

    def clear(self) -> None:
        self._pending.clear()
