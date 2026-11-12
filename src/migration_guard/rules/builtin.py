"""Built-in rules. Each rule documents *why* the operation is risky in production."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from migration_guard.models import Severity, Statement
from migration_guard.rules.base import IDENT, FileContext, Rule, RuleMatch, register

_I = re.IGNORECASE
POSTGRES = frozenset({"postgres"})

_ALTER_TABLE_RE = re.compile(
    rf"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({IDENT})\s+(.+)$", _I
)
_NOT_A_COLUMN = r"(?!(?:CONSTRAINT|INDEX|KEY|PRIMARY|FOREIGN|UNIQUE|CHECK|PARTITION)\b)"


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` outside parentheses: ``a NUMERIC(10,2), b INT`` -> 2 parts."""
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def alter_table_actions(stmt: Statement) -> tuple[str, list[str]] | None:
    match = _ALTER_TABLE_RE.match(stmt.normalized)
    if not match:
        return None
    return match.group(1), split_top_level(match.group(2))


class AlterTableRule(Rule):
    """Template Method: subclasses inspect one ALTER TABLE action at a time."""

    skip_new_tables = True

    def check(self, stmt: Statement, ctx: FileContext) -> Iterable[RuleMatch]:
        parsed = alter_table_actions(stmt)
        if not parsed:
            return
        table, actions = parsed
        if self.skip_new_tables and ctx.is_new_table(table):
            return
        for action in actions:
            yield from self.check_action(table, action, ctx)

    def check_action(self, table: str, action: str, ctx: FileContext) -> Iterator[RuleMatch]:
        raise NotImplementedError


@register
class DropTable(Rule):
    id = "MG001"
    name = "drop-table"
    severity = Severity.ERROR
    summary = "DROP TABLE permanently deletes data and breaks code still reading the table."
    _re = re.compile(rf"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?({IDENT}(?:\s*,\s*{IDENT})*)", _I)

    def check(self, stmt, ctx):
        match = self._re.match(stmt.normalized)
        if match:
            yield RuleMatch(
                f"Dropping table(s) {match.group(1)} is irreversible.",
                "Stop all reads/writes in a prior release, take a backup or rename the table "
                "first, and drop it in a later migration once nothing references it.",
            )


@register
class DropColumn(AlterTableRule):
    id = "MG002"
    name = "drop-column"
    severity = Severity.ERROR
    summary = "Dropping a column breaks running application instances that still select it."
    skip_new_tables = False
    _re = re.compile(rf"^DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?{_NOT_A_COLUMN}({IDENT})", _I)

    def check_action(self, table, action, ctx):
        match = self._re.match(action)
        if match:
            yield RuleMatch(
                f"Dropping column {match.group(1)} from {table} breaks old app versions during "
                "a rolling deploy and destroys its data.",
                "Use expand/contract: deploy code that no longer reads or writes the column, "
                "then drop it in a separate, later migration.",
            )


@register
class AlterColumnType(AlterTableRule):
    id = "MG003"
    name = "alter-column-type"
    severity = Severity.ERROR
    summary = "Changing a column type usually rewrites the table under an exclusive lock."
    _patterns = (
        re.compile(rf"^ALTER\s+(?:COLUMN\s+)?({IDENT})\s+(?:SET\s+DATA\s+)?TYPE\b", _I),
        re.compile(rf"^(?:MODIFY|CHANGE)\s+(?:COLUMN\s+)?({IDENT})\b", _I),
    )

    def check_action(self, table, action, ctx):
        for pattern in self._patterns:
            match = pattern.match(action)
            if match:
                yield RuleMatch(
                    f"Changing the type of {table}.{match.group(1)} can rewrite the whole table "
                    "while blocking reads and writes.",
                    "Add a new column with the target type, backfill it in batches, switch the "
                    "application over, then drop the old column.",
                )
                return


@register
class AddNotNullColumnWithoutDefault(AlterTableRule):
    id = "MG004"
    name = "add-not-null-column-without-default"
    severity = Severity.ERROR
    summary = "Adding a NOT NULL column with no default fails on non-empty tables."
    _re = re.compile(
        rf"^ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?{_NOT_A_COLUMN}({IDENT})\s+(.*)$", _I
    )

    def check_action(self, table, action, ctx):
        match = self._re.match(action)
        if not match:
            return
        definition = match.group(2)
        if re.search(r"\bNOT\s+NULL\b", definition, _I) and not re.search(
            r"\b(?:DEFAULT|IDENTITY|GENERATED)\b", definition, _I
        ):
            yield RuleMatch(
                f"Column {table}.{match.group(1)} is NOT NULL without a DEFAULT; the migration "
                "fails as soon as the table has rows.",
                "Add the column as nullable (or with a constant DEFAULT), backfill it, then add "
                "the NOT NULL constraint in a follow-up migration.",
            )


@register
class CreateIndexNonConcurrently(Rule):
    id = "MG005"
    name = "create-index-non-concurrently"
    severity = Severity.WARNING
    summary = "CREATE INDEX without CONCURRENTLY blocks writes for the whole build."
    dialects = POSTGRES
    _re = re.compile(
        rf"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY\b)"
        rf"(?:IF\s+NOT\s+EXISTS\s+)?(?:{IDENT}\s+)?ON\s+(?:ONLY\s+)?({IDENT})",
        _I,
    )

    def check(self, stmt, ctx):
        match = self._re.match(stmt.normalized)
        if match and not ctx.is_new_table(match.group(1)):
            yield RuleMatch(
                f"Building an index on {match.group(1)} without CONCURRENTLY blocks all writes "
                "to the table until the build finishes.",
                "Use CREATE INDEX CONCURRENTLY and run it outside a transaction block.",
            )


@register
class Rename(Rule):
    id = "MG006"
    name = "rename"
    severity = Severity.WARNING
    summary = "Renaming tables or columns breaks app instances that still use the old name."
    _mysql_re = re.compile(rf"^RENAME\s+TABLE\s+({IDENT})\s+TO\s+({IDENT})", _I)
    _column_re = re.compile(rf"^RENAME\s+(?:COLUMN\s+)?({IDENT})\s+TO\s+({IDENT})", _I)
    _table_re = re.compile(rf"^RENAME\s+TO\s+({IDENT})", _I)

    def check(self, stmt, ctx):
        match = self._mysql_re.match(stmt.normalized)
        if match:
            yield self._match(f"table {match.group(1)}", match.group(2))
            return
        parsed = alter_table_actions(stmt)
        if not parsed:
            return
        table, actions = parsed
        for action in actions:
            if m := self._table_re.match(action):
                yield self._match(f"table {table}", m.group(1))
            elif m := self._column_re.match(action):
                yield self._match(f"column {table}.{m.group(1)}", m.group(2))

    @staticmethod
    def _match(old: str, new: str) -> RuleMatch:
        return RuleMatch(
            f"Renaming {old} to {new} breaks application instances still using the old name "
            "during deployment.",
            "Add the new name alongside the old one (new column + dual writes, or a view), "
            "migrate readers, then remove the old name later.",
        )


@register
class Truncate(Rule):
    id = "MG007"
    name = "truncate"
    severity = Severity.ERROR
    summary = "TRUNCATE deletes every row and takes an ACCESS EXCLUSIVE lock."
    _re = re.compile(rf"^TRUNCATE\s+(?:TABLE\s+)?(?:ONLY\s+)?({IDENT})", _I)

    def check(self, stmt, ctx):
        match = self._re.match(stmt.normalized)
        if match:
            yield RuleMatch(
                f"TRUNCATE {match.group(1)} deletes all rows in a schema migration.",
                "Data deletion belongs in a reviewed, reversible data job, not a schema migration.",
            )


@register
class UnboundedDml(Rule):
    id = "MG008"
    name = "unbounded-update-or-delete"
    severity = Severity.ERROR
    summary = "UPDATE/DELETE without WHERE touches every row in one long transaction."
    _re = re.compile(rf"^(UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?({IDENT})", _I)

    def check(self, stmt, ctx):
        match = self._re.match(stmt.normalized)
        if match and not re.search(r"\bWHERE\b", stmt.normalized, _I):
            verb = match.group(1).split()[0].upper()
            yield RuleMatch(
                f"{verb} on {match.group(2)} has no WHERE clause and will touch every row.",
                "Add a WHERE clause, or backfill in small batches from an application job so "
                "locks and replication lag stay bounded.",
            )


@register
class ForeignKeyWithoutNotValid(AlterTableRule):
    id = "MG009"
    name = "foreign-key-without-not-valid"
    severity = Severity.WARNING
    summary = "Adding a validated foreign key scans the table while blocking writes."
    dialects = POSTGRES
    _re = re.compile(r"^ADD\s+(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\b", _I)

    def check_action(self, table, action, ctx):
        if self._re.match(action) and not re.search(r"\bNOT\s+VALID\b", action, _I):
            yield RuleMatch(
                f"Adding a foreign key on {table} validates every existing row under a lock.",
                "Add the constraint with NOT VALID, then run ALTER TABLE ... VALIDATE CONSTRAINT "
                "in a separate migration.",
            )


@register
class SetNotNull(AlterTableRule):
    id = "MG010"
    name = "set-not-null"
    severity = Severity.WARNING
    summary = "SET NOT NULL scans the full table under an ACCESS EXCLUSIVE lock."
    dialects = POSTGRES
    _re = re.compile(rf"^ALTER\s+(?:COLUMN\s+)?({IDENT})\s+SET\s+NOT\s+NULL\b", _I)

    def check_action(self, table, action, ctx):
        match = self._re.match(action)
        if match:
            yield RuleMatch(
                f"SET NOT NULL on {table}.{match.group(1)} scans every row while blocking "
                "reads and writes.",
                "Add CHECK (col IS NOT NULL) NOT VALID, VALIDATE it separately, then SET NOT "
                "NULL (Postgres 12+ skips the scan) and drop the CHECK.",
            )


@register
class CheckConstraintWithoutNotValid(AlterTableRule):
    id = "MG011"
    name = "check-constraint-without-not-valid"
    severity = Severity.WARNING
    summary = "Adding a validated CHECK constraint scans the table under a lock."
    dialects = POSTGRES
    _re = re.compile(r"^ADD\s+(?:CONSTRAINT\s+\S+\s+)?CHECK\b", _I)

    def check_action(self, table, action, ctx):
        if self._re.match(action) and not re.search(r"\bNOT\s+VALID\b", action, _I):
            yield RuleMatch(
                f"Adding a CHECK constraint on {table} validates every row under a lock.",
                "Add it with NOT VALID, then VALIDATE CONSTRAINT in a separate migration.",
            )


@register
class MissingLockTimeout(Rule):
    id = "MG012"
    name = "missing-lock-timeout"
    severity = Severity.WARNING
    summary = (
        "Lock-taking DDL without lock_timeout can queue behind a long query and stall traffic."
    )
    dialects = POSTGRES
    _lock_timeout_re = re.compile(r"^SET\s+(?:LOCAL\s+)?lock_timeout\b", _I)
    _locking_re = re.compile(r"^(?:ALTER\s+TABLE|DROP\s+TABLE|TRUNCATE)\b", _I)

    def check(self, stmt, ctx):
        if self.id in ctx.reported_once or not self._locking_re.match(stmt.normalized):
            return
        parsed = alter_table_actions(stmt)
        if parsed and ctx.is_new_table(parsed[0]):
            return
        if any(self._lock_timeout_re.match(s.normalized) for s in ctx.statements_before(stmt)):
            return
        ctx.reported_once.add(self.id)
        yield RuleMatch(
            "This migration takes an ACCESS EXCLUSIVE lock without setting lock_timeout; if it "
            "waits behind a long-running query, every other query on the table queues behind it.",
            "Start the migration with SET lock_timeout = '5s'; and retry on timeout.",
        )


@register
class DropIndexNonConcurrently(Rule):
    id = "MG013"
    name = "drop-index-non-concurrently"
    severity = Severity.WARNING
    summary = "DROP INDEX without CONCURRENTLY blocks reads and writes on the table."
    dialects = POSTGRES
    _re = re.compile(rf"^DROP\s+INDEX\s+(?!CONCURRENTLY\b)(?:IF\s+EXISTS\s+)?({IDENT})", _I)

    def check(self, stmt, ctx):
        match = self._re.match(stmt.normalized)
        if match:
            yield RuleMatch(
                f"Dropping index {match.group(1)} without CONCURRENTLY takes an exclusive lock "
                "on its table.",
                "Use DROP INDEX CONCURRENTLY outside a transaction block.",
            )


@register
class AddUniqueOrPrimaryKey(AlterTableRule):
    id = "MG014"
    name = "add-unique-constraint-without-index"
    severity = Severity.WARNING
    summary = "Adding UNIQUE/PRIMARY KEY builds an index while blocking writes."
    dialects = POSTGRES
    _re = re.compile(r"^ADD\s+(?:CONSTRAINT\s+\S+\s+)?(?:UNIQUE|PRIMARY\s+KEY)\b", _I)

    def check_action(self, table, action, ctx):
        if self._re.match(action) and not re.search(r"\bUSING\s+INDEX\b", action, _I):
            yield RuleMatch(
                f"Adding a UNIQUE/PRIMARY KEY constraint on {table} builds its index under a "
                "write-blocking lock.",
                "CREATE UNIQUE INDEX CONCURRENTLY first, then ADD CONSTRAINT ... USING INDEX.",
            )
