"""Output formats. Each reporter turns a Report into a string.

* ``text``     - human-readable, for terminals
* ``json``     - stable machine-readable schema, for scripts and other tools
* ``github``   - GitHub Actions workflow commands (inline PR annotations)
* ``sarif``    - SARIF 2.1.0, for GitHub code scanning and other security dashboards
* ``markdown`` - a PR comment / job summary
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable

from migration_guard import __version__
from migration_guard.analyzer import Report
from migration_guard.models import Finding, Severity
from migration_guard.rules import all_rules

_COLORS = {Severity.ERROR: "\033[31m", Severity.WARNING: "\033[33m", Severity.INFO: "\033[36m"}
_RESET, _BOLD, _DIM = "\033[0m", "\033[1m", "\033[2m"


def _summary_line(report: Report) -> str:
    counts = Counter(f.severity for f in report.findings)
    files = len(report.files_scanned)
    parts = [
        f"{counts[s]} {s}{'s' if counts[s] != 1 else ''}" for s in sorted(counts, reverse=True)
    ]
    noun = "file" if files == 1 else "files"
    if not parts:
        return f"No risky migrations found in {files} {noun}."
    return f"{', '.join(parts)} in {files} {noun} scanned."


def render_text(report: Report, color: bool = False) -> str:
    def paint(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if color else text

    lines: list[str] = []
    current_file = None
    for f in report.findings:
        if f.file != current_file:
            if current_file is not None:
                lines.append("")
            lines.append(paint(f.file, _BOLD))
            current_file = f.file
        sev = paint(f"{str(f.severity):<7}", _COLORS[f.severity])
        lines.append(f"  {f.line:>4}  {sev}  {f.rule_id} {f.rule_name}")
        lines.append(f"        {f.message}")
        lines.append(paint(f"        fix: {f.suggestion}", _DIM))
    for err in report.errors:
        lines.append(paint(f"error: {err.file}: {err.message}", _COLORS[Severity.ERROR]))
    if lines:
        lines.append("")
    lines.append(_summary_line(report))
    return "\n".join(lines)


def render_json(report: Report) -> str:
    payload = {
        "version": __version__,
        "summary": {
            "files_scanned": len(report.files_scanned),
            "findings": len(report.findings),
            "by_severity": {
                str(s): sum(f.severity == s for f in report.findings) for s in Severity
            },
        },
        "findings": [f.to_dict() for f in report.findings],
        "errors": [{"file": e.file, "message": e.message} for e in report.errors],
    }
    return json.dumps(payload, indent=2)


def _gh_escape(value: str, prop: bool = False) -> str:
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


def render_github(report: Report) -> str:
    level = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "notice"}
    lines = []
    for f in report.findings:
        title = _gh_escape(f"{f.rule_id} {f.rule_name}", prop=True)
        body = _gh_escape(f"{f.message} Fix: {f.suggestion}")
        lines.append(
            f"::{level[f.severity]} file={_gh_escape(f.file, True)},line={f.line},"
            f"title={title}::{body}"
        )
    for e in report.errors:
        lines.append(f"::error file={_gh_escape(e.file, True)}::{_gh_escape(e.message)}")
    lines.append(_summary_line(report))
    return "\n".join(lines)


def render_sarif(report: Report) -> str:
    level = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.INFO: "note"}
    rules = [
        {
            "id": r.id,
            "name": r.name,
            "shortDescription": {"text": r.summary},
            "defaultConfiguration": {"level": level[r.severity]},
        }
        for r in all_rules()
    ]
    results = [
        {
            "ruleId": f.rule_id,
            "level": level[f.severity],
            "message": {"text": f"{f.message} Fix: {f.suggestion}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file},
                        "region": {"startLine": f.line},
                    }
                }
            ],
        }
        for f in report.findings
    ]
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "migration-guard",
                        "version": __version__,
                        "informationUri": "https://github.com/Gkoushik17/MigrationGuard",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


def render_markdown(report: Report) -> str:
    icon = {Severity.ERROR: "🔴", Severity.WARNING: "🟡", Severity.INFO: "🔵"}
    out = ["### MigrationGuard", "", _summary_line(report)]
    if report.findings:
        out += ["", "| | Rule | Location | Problem | Suggested fix |", "|---|---|---|---|---|"]
        for f in report.findings:
            cells = [
                icon[f.severity],
                f"`{f.rule_id}` {f.rule_name}",
                f"`{f.file}:{f.line}`",
                f.message.replace("|", "\\|"),
                f.suggestion.replace("|", "\\|"),
            ]
            out.append("| " + " | ".join(cells) + " |")
    if report.errors:
        out += ["", "**Files that could not be checked:**"]
        out += [f"- `{e.file}`: {e.message}" for e in report.errors]
    return "\n".join(out)


REPORTERS: dict[str, Callable[[Report], str]] = {
    "text": render_text,
    "json": render_json,
    "github": render_github,
    "sarif": render_sarif,
    "markdown": render_markdown,
}


def failing(findings: list[Finding], threshold: Severity) -> list[Finding]:
    return [f for f in findings if f.severity >= threshold]
