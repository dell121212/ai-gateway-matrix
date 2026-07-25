"""Compose-compatible .env values without accidental interpolation."""

from __future__ import annotations

import re
from pathlib import Path

_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_UNQUOTED_VALUE_RE = re.compile(r"[A-Za-z0-9_./:+,=@%~-]*")


def decode_value(value: str) -> str:
    """Decode the quoting forms this project writes and common Compose env forms."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("\\'", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def encode_value(value: str) -> str:
    """Quote values containing ``$``/spaces so Compose treats them literally."""
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise ValueError("环境变量值不能包含换行或 NUL")
    if _UNQUOTED_VALUE_RE.fullmatch(value):
        return value
    return "'" + value.replace("'", "\\'") + "'"


def parse_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not _KEY_RE.fullmatch(key):
        return None
    return key, decode_value(value)


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_line(line)
        if parsed is not None:
            result[parsed[0]] = parsed[1]
    return result
