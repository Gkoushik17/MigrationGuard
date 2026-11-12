"""Core value types shared across the parser, rules, analyzer and reporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    """Ordered severity levels. Comparison operators reflect risk (ERROR > WARNING)."""

    INFO = 1
    WARNING = 2
    ERROR = 3

    @classmethod
    def parse(cls, value: str) -> Severity:
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(
                f"unknown severity {value!r}; expected one of: info, warning, error"
            ) from None

    def __str__(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Statement:
    """A single SQL statement extracted from a migration file.

    ``normalized`` has comments removed, string literal contents masked and
    whitespace collapsed, so rules can pattern-match without false positives
    from text such as ``INSERT INTO notes VALUES ('drop table users')``.
    """

    text: str
    normalized: str
    start_line: int
    end_line: int
    suppressed: frozenset[str] = field(default_factory=frozenset)

    def is_suppressed(self, rule_id: str) -> bool:
        return "*" in self.suppressed or rule_id in self.suppressed


@dataclass(frozen=True)
class Finding:
    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    suggestion: str
    file: str
    line: int
    statement: str

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": str(self.severity),
            "message": self.message,
            "suggestion": self.suggestion,
            "file": self.file,
            "line": self.line,
            "statement": self.statement,
        }
