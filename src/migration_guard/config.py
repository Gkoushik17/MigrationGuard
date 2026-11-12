"""Configuration loading.

Looked up, in order: an explicit ``--config`` path, ``.migration-guard.toml``,
then ``[tool.migration-guard]`` in ``pyproject.toml``. Every key is optional;
the defaults are safe for a PostgreSQL project.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from migration_guard.models import Severity

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

DIALECTS = ("postgres", "mysql", "generic")
DEFAULT_INCLUDE = ("**/migrations/**/*.sql", "**/migrate/**/*.sql")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    dialect: str = "postgres"
    include: tuple[str, ...] = DEFAULT_INCLUDE
    exclude: tuple[str, ...] = ()
    fail_on: Severity = Severity.ERROR
    disable: frozenset[str] = frozenset()
    severity_overrides: dict[str, Severity] = field(default_factory=dict)
    source: str | None = None

    def with_overrides(self, **changes: Any) -> Config:
        return replace(self, **{k: v for k, v in changes.items() if v is not None})


def load_config(explicit: Path | None = None, cwd: Path | None = None) -> Config:
    cwd = cwd or Path.cwd()
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        data = _read_toml(explicit)
        if explicit.name == "pyproject.toml":
            data = data.get("tool", {}).get("migration-guard", {})
        return _from_mapping(data, str(explicit))

    dedicated = cwd / ".migration-guard.toml"
    if dedicated.is_file():
        return _from_mapping(_read_toml(dedicated), str(dedicated))

    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        section = _read_toml(pyproject).get("tool", {}).get("migration-guard")
        if section is not None:
            return _from_mapping(section, str(pyproject))

    return Config()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc


def _from_mapping(data: dict[str, Any], source: str) -> Config:
    from migration_guard.rules import rule_ids  # local import avoids a cycle

    known = set(rule_ids())
    allowed_keys = {"dialect", "include", "exclude", "fail_on", "disable", "severity"}
    unknown = set(data) - allowed_keys
    if unknown:
        raise ConfigError(f"{source}: unknown key(s): {', '.join(sorted(unknown))}")

    def rule_list(key: str) -> frozenset[str]:
        values = frozenset(str(v).upper() for v in data.get(key, []))
        bad = values - known
        if bad:
            raise ConfigError(f"{source}: unknown rule id(s) in {key!r}: {', '.join(sorted(bad))}")
        return values

    dialect = str(data.get("dialect", "postgres")).lower()
    if dialect not in DIALECTS:
        raise ConfigError(f"{source}: dialect must be one of {', '.join(DIALECTS)}")

    overrides: dict[str, Severity] = {}
    for rule_id, level in data.get("severity", {}).items():
        if rule_id.upper() not in known:
            raise ConfigError(f"{source}: unknown rule id in [severity]: {rule_id}")
        try:
            overrides[rule_id.upper()] = Severity.parse(str(level))
        except ValueError as exc:
            raise ConfigError(f"{source}: {exc}") from exc

    try:
        fail_on = Severity.parse(str(data.get("fail_on", "error")))
    except ValueError as exc:
        raise ConfigError(f"{source}: {exc}") from exc

    return Config(
        dialect=dialect,
        include=tuple(data.get("include", DEFAULT_INCLUDE)),
        exclude=tuple(data.get("exclude", ())),
        fail_on=fail_on,
        disable=rule_list("disable"),
        severity_overrides=overrides,
        source=source,
    )
