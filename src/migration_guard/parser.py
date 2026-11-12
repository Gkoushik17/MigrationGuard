"""A small, dependency-free SQL statement splitter.

It understands enough SQL lexing to split a migration into statements safely:
single-quoted strings, double-quoted identifiers, ``--`` and ``/* */``
comments, and PostgreSQL dollar-quoted bodies (``$$ ... $$``, ``$fn$ ... $fn$``).
It also collects inline suppression directives:

    -- migration-guard: ignore MG001, MG002   (applies to one statement)
    -- migration-guard: ignore                (all rules, one statement)
    -- migration-guard: ignore-file           (whole file)

A directive on its own line applies to the next statement; a directive that
trails a statement on the same line applies to that statement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from migration_guard.models import Statement

_DIRECTIVE_RE = re.compile(
    r"migration-guard:\s*(?P<kind>ignore-file|ignore)\b(?P<ids>[A-Za-z0-9_,\s]*)",
    re.IGNORECASE,
)
_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Directive:
    rule_ids: frozenset[str]
    target: int  # index of the statement the directive applies to


@dataclass(frozen=True)
class ParsedFile:
    statements: list[Statement]
    ignore_file: bool


class SqlParseError(ValueError):
    """Raised for input that cannot be split, such as an unterminated string."""


def parse_sql(sql: str) -> ParsedFile:
    statements_raw: list[tuple[str, str, int, int]] = []
    directives: list[_Directive] = []
    ignore_file = False

    i = 0
    n = len(sql)
    line = 1
    last_code_line = 0  # line of the most recent non-comment, non-space char

    start_idx: int | None = None
    start_line = 0
    normalized: list[str] = []

    def begin_statement() -> None:
        nonlocal start_idx, start_line
        if start_idx is None:
            start_idx = i
            start_line = line

    def handle_comment(body: str, comment_line: int) -> None:
        nonlocal ignore_file
        match = _DIRECTIVE_RE.search(body)
        if not match:
            return
        if match.group("kind").lower() == "ignore-file":
            ignore_file = True
            return
        ids = frozenset(token.upper() for token in re.split(r"[\s,]+", match.group("ids")) if token)
        # Own-line comments, and any comment inside an unfinished statement, apply
        # to the current/next statement; a comment trailing a finished statement
        # on the same line applies to that statement.
        own_line = last_code_line != comment_line
        target = len(statements_raw)
        if not own_line and start_idx is None:
            target -= 1
        directives.append(_Directive(rule_ids=ids or frozenset({"*"}), target=target))

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if ch == "\n":
            line += 1
            normalized.append(" ")
            i += 1
            continue
        if ch.isspace():
            normalized.append(" ")
            i += 1
            continue

        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            end = n if end == -1 else end
            handle_comment(sql[i + 2 : end], line)
            normalized.append(" ")
            i = end
            continue

        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                raise SqlParseError(f"unterminated block comment starting on line {line}")
            body = sql[i + 2 : end]
            handle_comment(body, line)
            line += body.count("\n")
            normalized.append(" ")
            i = end + 2
            continue

        # Everything below is code.
        begin_statement()
        last_code_line = line

        if ch == "'":
            j = i + 1
            while True:
                j = sql.find("'", j)
                if j == -1:
                    raise SqlParseError(f"unterminated string literal starting on line {line}")
                if j + 1 < n and sql[j + 1] == "'":  # escaped quote ''
                    j += 2
                    continue
                break
            line += sql.count("\n", i, j)
            normalized.append("'?'")
            i = j + 1
            continue

        if ch == '"':
            j = sql.find('"', i + 1)
            if j == -1:
                raise SqlParseError(f"unterminated quoted identifier starting on line {line}")
            line += sql.count("\n", i, j)
            normalized.append(sql[i : j + 1])
            i = j + 1
            continue

        if ch == "$":
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag:
                close = sql.find(tag.group(0), tag.end())
                if close == -1:
                    raise SqlParseError(f"unterminated dollar-quoted string on line {line}")
                line += sql.count("\n", i, close)
                normalized.append("'?'")
                i = close + len(tag.group(0))
                continue

        if ch == ";":
            assert start_idx is not None
            text = sql[start_idx : i + 1].strip()
            norm = _WS_RE.sub(" ", "".join(normalized)).strip()
            if norm:
                statements_raw.append((text, norm, start_line, line))
            start_idx = None
            normalized = []
            i += 1
            continue

        normalized.append(ch)
        i += 1

    # Trailing statement without a terminating semicolon.
    norm = _WS_RE.sub(" ", "".join(normalized)).strip()
    if start_idx is not None and norm:
        statements_raw.append((sql[start_idx:].strip(), norm, start_line, line))

    return ParsedFile(
        statements=_attach_directives(statements_raw, directives),
        ignore_file=ignore_file,
    )


def _attach_directives(
    raw: list[tuple[str, str, int, int]], directives: list[_Directive]
) -> list[Statement]:
    suppressed: list[set[str]] = [set() for _ in raw]
    for directive in directives:
        if 0 <= directive.target < len(raw):
            suppressed[directive.target] |= directive.rule_ids

    return [
        Statement(
            text=text, normalized=norm, start_line=start, end_line=end, suppressed=frozenset(ids)
        )
        for (text, norm, start, end), ids in zip(raw, suppressed, strict=True)
    ]
