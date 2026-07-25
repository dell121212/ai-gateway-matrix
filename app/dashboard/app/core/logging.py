"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;\"']+)"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.groups == 2:
            out = pat.sub(r"\1***", out)
        else:
            out = pat.sub("***REDACTED***", out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact(str(a)) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:
            pass
        return True


def setup_logging(name: str = "private_api") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger


def safe_meta(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    blocked = {"authorization", "api_key", "password", "token", "secret", "content", "messages"}
    out: dict[str, Any] = {}
    for k, v in data.items():
        if str(k).lower() in blocked:
            out[k] = "***"
        elif isinstance(v, str) and len(v) > 500:
            out[k] = v[:100] + "…"
        else:
            out[k] = v
    return out
