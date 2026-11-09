"""Helpers for preparing Phase 1 AT command bytes."""

from __future__ import annotations


TERMINATORS: dict[str, bytes] = {
    "Ninguna": b"",
    "CR": b"\r",
    "LF": b"\n",
    "CRLF": b"\r\n",
}


def encode_at_command(command: str, terminator: str = "CR") -> bytes:
    """Encode an AT command as ASCII and append the selected terminator."""
    try:
        suffix = TERMINATORS[terminator]
    except KeyError as exc:
        raise ValueError(f"Terminación no válida: {terminator}") from exc
    return command.encode("ascii") + suffix


def encode_at_commands(text: str, terminator: str = "CR") -> list[bytes]:
    """Encode non-empty lines as separate AT commands with the selected suffix."""
    commands = [line.strip() for line in text.splitlines() if line.strip()]
    return [encode_at_command(command, terminator) for command in commands]
