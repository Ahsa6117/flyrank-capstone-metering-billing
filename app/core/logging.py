"""Logging with secret redaction.

Requirement S6: secrets are env-only and **never logged**. A key can leak through
an exception message or a debug dump long after the code that handled it was
reviewed, so redaction happens at the logging layer -- the last gate before text
leaves the process.
"""

from __future__ import annotations

import logging
import re

#: Anything that looks like a credential. Kept deliberately broad.
_SECRET_PATTERNS = [
    re.compile(r"\b(sk|rk|pk)_(test|live)_[A-Za-z0-9]+"),
    re.compile(r"\bwhsec_[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]+"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[=:]\s*\S+"),
]

_REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites the formatted message and args of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
    )
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
