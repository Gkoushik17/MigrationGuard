"""Runs the enabled rules over migration files and produces findings."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from migration_guard.config import Config
from migration_guard.models import Finding
from migration_guard.parser import SqlParseError, parse_sql
from migration_guard.rules import FileContext, Rule, all_rules


@dataclass(frozen=True)
class FileError:
    file: str
    message: str


@dataclass(frozen=True)
class Report:
    findings: list[Finding]
    files_scanned: list[str]
    errors: list[FileError]


class Analyzer:
    def __init__(self, config: Config, rules: Sequence[Rule] | None = None) -> None:
        self.config = config
        candidates = rules if rules is not None else all_rules()
        self.rules = [
            r for r in candidates if r.id not in config.disable and r.applies_to(config.dialect)
        ]

    def analyze_text(self, sql: str, path: str = "<stdin>") -> list[Finding]:
        parsed = parse_sql(sql)
        if parsed.ignore_file:
            return []
        ctx = FileContext.build(path, self.config.dialect, parsed.statements)
        findings: list[Finding] = []
        for stmt in parsed.statements:
            for rule in self.rules:
                if stmt.is_suppressed(rule.id):
                    continue
                for match in rule.check(stmt, ctx):
                    findings.append(
                        Finding(
                            rule_id=rule.id,
                            rule_name=rule.name,
                            severity=self.config.severity_overrides.get(rule.id, rule.severity),
                            message=match.message,
                            suggestion=match.suggestion,
                            file=path,
                            line=stmt.start_line,
                            statement=stmt.text,
                        )
                    )
        return findings

    def analyze_files(self, paths: Iterable[Path], root: Path | None = None) -> Report:
        findings: list[Finding] = []
        scanned: list[str] = []
        errors: list[FileError] = []
        for path in paths:
            display = _display_path(path, root)
            try:
                sql = path.read_text(encoding="utf-8")
                findings.extend(self.analyze_text(sql, display))
            except (OSError, UnicodeDecodeError, SqlParseError) as exc:
                errors.append(FileError(display, str(exc)))
                continue
            scanned.append(display)
        findings.sort(key=lambda f: (f.file, f.line, f.rule_id))
        return Report(findings=findings, files_scanned=scanned, errors=errors)


def _display_path(path: Path, root: Path | None) -> str:
    root = root or Path.cwd()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def discover_files(targets: Sequence[Path], config: Config, root: Path) -> list[Path]:
    """Expand CLI targets into .sql files, honouring include/exclude globs.

    Explicit files are always checked; directories are searched recursively.
    With no targets, ``config.include`` globs are evaluated from ``root``.
    """
    found: set[Path] = set()
    if targets:
        for target in targets:
            if target.is_dir():
                found.update(p for p in target.rglob("*.sql") if p.is_file())
            elif target.is_file():
                found.add(target)
            else:
                raise FileNotFoundError(f"no such file or directory: {target}")
    else:
        for pattern in config.include:
            found.update(p for p in root.glob(pattern) if p.is_file())

    def excluded(p: Path) -> bool:
        rel = _display_path(p, root)
        return any(_glob_match(rel, pattern) for pattern in config.exclude)

    return sorted(p for p in found if not excluded(p))


def filter_to_included(paths: Iterable[Path], config: Config, root: Path) -> list[Path]:
    """Keep only files matching ``config.include`` (used for git-diff mode)."""
    keep = []
    for p in paths:
        rel = _display_path(p, root)
        if p.suffix.lower() != ".sql":
            continue
        if any(_glob_match(rel, pattern) for pattern in config.include) and not any(
            _glob_match(rel, pattern) for pattern in config.exclude
        ):
            keep.append(p)
    return sorted(keep)


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob: ``**/`` spans zero or more directories,
    ``*`` and ``?`` never cross a ``/``. (PurePath.match lacks ``**`` before 3.13.)"""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _glob_match(rel: str, pattern: str) -> bool:
    return _glob_regex(pattern).match(rel) is not None
