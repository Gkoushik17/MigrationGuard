"""Rule abstraction, per-file context and the rule registry.

Rules are small Strategy objects: each one inspects a single statement (with
read-only access to the rest of the file via :class:`FileContext`) and yields
zero or more :class:`RuleMatch` objects. New rules self-register with the
``@register`` decorator, so adding a check never requires touching the analyzer.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import ClassVar

from migration_guard.models import Severity, Statement

IDENT = r'(?:"[^"]+"|[A-Za-z_][\w$]*)(?:\.(?:"[^"]+"|[A-Za-z_][\w$]*))*'

_CREATE_TABLE_RE = re.compile(
    rf"^CREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?({IDENT})",
    re.IGNORECASE,
)


def canonical_name(identifier: str) -> str:
    """``"Public"."Users"`` and ``public.users`` compare equal; schema is dropped."""
    last = identifier.split(".")[-1]
    return last.strip('"').lower()


@dataclass(frozen=True)
class RuleMatch:
    message: str
    suggestion: str


@dataclass
class FileContext:
    path: str
    dialect: str
    statements: list[Statement]
    created_tables: set[str] = field(default_factory=set)
    # Lets file-level rules (e.g. "missing lock_timeout") report only once.
    reported_once: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, path: str, dialect: str, statements: list[Statement]) -> FileContext:
        created = set()
        for stmt in statements:
            match = _CREATE_TABLE_RE.match(stmt.normalized)
            if match:
                created.add(canonical_name(match.group(1)))
        return cls(path=path, dialect=dialect, statements=statements, created_tables=created)

    def is_new_table(self, identifier: str) -> bool:
        """Tables created in the same migration are empty, so locks and rewrites are harmless."""
        return canonical_name(identifier) in self.created_tables

    def statements_before(self, stmt: Statement) -> Iterator[Statement]:
        for other in self.statements:
            if other is stmt:
                return
            yield other


class Rule(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    severity: ClassVar[Severity]
    summary: ClassVar[str]
    # None means the rule applies to every dialect.
    dialects: ClassVar[frozenset[str] | None] = None

    def applies_to(self, dialect: str) -> bool:
        return self.dialects is None or dialect in self.dialects

    @abstractmethod
    def check(self, stmt: Statement, ctx: FileContext) -> Iterable[RuleMatch]:
        """Yield a RuleMatch for every problem found in ``stmt``."""


_REGISTRY: dict[str, type[Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    rule_id = cls.id.upper()
    if rule_id in _REGISTRY:
        raise ValueError(f"duplicate rule id {rule_id}")
    _REGISTRY[rule_id] = cls
    return cls


def all_rules() -> list[Rule]:
    return [cls() for _, cls in sorted(_REGISTRY.items())]


def rule_ids() -> list[str]:
    return sorted(_REGISTRY)
