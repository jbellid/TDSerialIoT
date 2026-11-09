"""Sequential AT command queue and response classification."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal

TerminalResult = Literal["success", "error"]

_TERMINAL_SUCCESS = re.compile(rb"OK", re.IGNORECASE)
_TERMINAL_ERROR = re.compile(
    rb"(?:ERROR|\+CME ERROR(?::[ \t]*.*)?|\+CMS ERROR(?::[ \t]*.*)?)",
    re.IGNORECASE,
)
_CAOPEN_COMMAND = re.compile(rb"^AT\+CAOPEN\s*=\s*(\d+)", re.IGNORECASE)


def classify_terminal_response(line: bytes) -> TerminalResult | None:
    """Classify a complete modem result line without matching partial text."""
    response = line.strip(b"\r\n \t")
    if _TERMINAL_SUCCESS.fullmatch(response):
        return "success"
    if _TERMINAL_ERROR.fullmatch(response):
        return "error"
    return None


def is_terminal_response(line: bytes) -> bool:
    """Return whether a complete modem response line is terminal."""
    return classify_terminal_response(line) is not None


@dataclass(frozen=True)
class InteractionPlan:
    """Optional prompt/payload exchange, leaving transport ownership centralized."""

    prompt: bytes
    payload: bytes
    response_timeout_ms: int = 30_000


@dataclass(frozen=True)
class AtCommand:
    """One encoded AT command and its operation-specific completion policy."""

    data: bytes
    timeout_ms: int = 10_000
    confirmation_prefix: bytes | None = None
    confirmation_succeeded: Callable[[bytes], bool] | None = None
    interaction: InteractionPlan | None = None
    purpose: str | None = None


@dataclass(frozen=True)
class QueueEvent:
    kind: Literal["prompt", "success", "error"]
    command: AtCommand
    response: bytes
    payload: bytes | None = None


def make_at_command(
    data: bytes, timeout_ms: int = 10_000, purpose: str | None = None
) -> AtCommand:
    """Attach known multi-stage modem completion rules to an encoded command."""
    command_text = data.rstrip(b"\r\n")
    match = _CAOPEN_COMMAND.match(command_text)
    if match is None:
        return AtCommand(data=data, timeout_ms=timeout_ms, purpose=purpose)
    connection_id = match.group(1)
    prefix = b"+CAOPEN: " + connection_id + b","

    def open_succeeded(response: bytes) -> bool:
        result = response.strip(b"\r\n \t")[len(prefix) :].split(b",", 1)[0].strip()
        return result == b"0"

    return AtCommand(
        data=data,
        timeout_ms=timeout_ms,
        confirmation_prefix=prefix,
        confirmation_succeeded=open_succeeded,
        purpose=purpose,
    )


class AtCommandQueue:
    """Keep one command active until all required results arrive or it times out."""

    def __init__(self) -> None:
        self._pending: deque[AtCommand] = deque()
        self.active_spec: AtCommand | None = None
        self.paused = False
        self.completed_count = 0
        self.error_count = 0
        self.total_count = 0
        self.cancelled_count = 0
        self.cancel_requested = False
        self._success_seen = False
        self._confirmation_result: bool | None = None
        self._prompt_sent = False

    @property
    def active(self) -> bytes | None:
        return self.active_spec.data if self.active_spec is not None else None

    @property
    def is_busy(self) -> bool:
        return self.active_spec is not None or bool(self._pending)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def executed_count(self) -> int:
        return self.completed_count + self.error_count

    def enqueue(
        self, commands: list[AtCommand], *, priority: bool = False
    ) -> AtCommand | None:
        if not commands:
            return None
        if not self.is_busy and not self.paused:
            self.completed_count = 0
            self.error_count = 0
            self.total_count = 0
            self.cancelled_count = 0
            self.cancel_requested = False
        if priority:
            self._pending.extendleft(reversed(commands))
        else:
            self._pending.extend(commands)
        self.total_count += len(commands)
        return self.start_next()

    def start_next(self) -> AtCommand | None:
        if (
            self.paused
            or self.cancel_requested
            or self.active_spec is not None
            or not self._pending
        ):
            return None
        self.active_spec = self._pending.popleft()
        self._success_seen = False
        self._confirmation_result = None
        self._prompt_sent = False
        return self.active_spec

    def accept_line(self, line: bytes, complete: bool = True) -> QueueEvent | None:
        command = self.active_spec
        if command is None:
            return None

        response = line.strip(b"\r\n")
        if (
            command.interaction is not None
            and not self._prompt_sent
            and response.strip() == command.interaction.prompt
        ):
            self._prompt_sent = True
            return QueueEvent("prompt", command, line, command.interaction.payload)

        if not complete:
            return None

        if command.confirmation_prefix is not None and response.startswith(
            command.confirmation_prefix
        ):
            if command.confirmation_succeeded is None:
                self._confirmation_result = True
            else:
                self._confirmation_result = command.confirmation_succeeded(response)
            if not self._confirmation_result:
                return self._finish("error", line)

        terminal = classify_terminal_response(line)
        if terminal == "error":
            return self._finish("error", line)
        if terminal == "success":
            self._success_seen = True
        if terminal == "success" and command.confirmation_prefix is None:
            return self._finish("success", line)
        if self._success_seen and self._confirmation_result is True:
            return self._finish("success", line)
        return None

    def timeout(self) -> AtCommand | None:
        """Record the active operation as failed and pause without advancing."""
        command = self.active_spec
        if command is None:
            return None
        self.active_spec = None
        self.error_count += 1
        self.paused = True
        return command

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> AtCommand | None:
        if self.cancel_requested:
            return None
        self.paused = False
        return self.start_next()

    def cancel_pending(self) -> int:
        """Cancel queued commands but allow any active operation to finish."""
        cancelled = len(self._pending)
        self._pending.clear()
        self.cancelled_count += cancelled
        self.cancel_requested = True
        self.paused = self.active_spec is not None
        return cancelled

    def cancel_pending_purpose(self, prefix: str) -> int:
        retained = deque()
        cancelled = 0
        while self._pending:
            command = self._pending.popleft()
            if command.purpose is not None and command.purpose.startswith(prefix):
                cancelled += 1
            else:
                retained.append(command)
        self._pending = retained
        self.cancelled_count += cancelled
        return cancelled

    def finish_cancellation(self) -> None:
        if self.cancel_requested and self.active_spec is None:
            self.paused = False

    def clear(self) -> int:
        discarded = len(self._pending) + (self.active_spec is not None)
        self._pending.clear()
        self.active_spec = None
        self.paused = False
        self.cancel_requested = False
        self.cancelled_count += discarded
        self._reset_active_state()
        return discarded

    def _finish(self, result: TerminalResult, response: bytes) -> QueueEvent:
        command = self.active_spec
        if command is None:
            raise RuntimeError("No hay ningún comando AT activo")
        self.active_spec = None
        if result == "success":
            self.completed_count += 1
        else:
            self.error_count += 1
        self._reset_active_state()
        return QueueEvent(result, command, response)

    def _reset_active_state(self) -> None:
        self._success_seen = False
        self._confirmation_result = None
        self._prompt_sent = False
