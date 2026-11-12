import json

import pytest

from migration_guard.analyzer import FileError, Report
from migration_guard.models import Finding, Severity
from migration_guard.reporters import REPORTERS, failing, render_github, render_text


def make_finding(severity=Severity.ERROR, file="db/m1.sql", line=3, message="Boom, x: y"):
    return Finding(
        "MG001", "drop-table", severity, message, "Do it later.", file, line, "DROP TABLE x;"
    )


@pytest.fixture
def report():
    return Report(
        findings=[make_finding(), make_finding(Severity.WARNING, line=9)],
        files_scanned=["db/m1.sql", "db/m2.sql"],
        errors=[],
    )


def test_text_groups_by_file_and_summarises(report):
    out = render_text(report)
    assert out.count("db/m1.sql") == 1
    assert "1 error, 1 warning in 2 files scanned." in out
    assert "\033[" not in out


def test_text_color_and_clean_summary():
    clean = Report([], ["a.sql"], [])
    assert render_text(clean) == "No risky migrations found in 1 file."
    assert "\033[31m" in render_text(Report([make_finding()], ["a.sql"], []), color=True)


def test_json_is_machine_readable(report):
    data = json.loads(REPORTERS["json"](report))
    assert data["summary"] == {
        "files_scanned": 2,
        "findings": 2,
        "by_severity": {"info": 0, "warning": 1, "error": 1},
    }
    assert data["findings"][0]["rule_id"] == "MG001"


def test_github_annotations_escape_special_characters():
    report = Report([make_finding(message="50% done\nnext")], ["db/m1.sql"], [])
    line = render_github(report).splitlines()[0]
    assert line.startswith("::error file=db/m1.sql,line=3,title=MG001 drop-table::")
    assert "50%25 done%0Anext" in line


def test_sarif_structure(report):
    sarif = json.loads(REPORTERS["sarif"](report))
    run = sarif["runs"][0]
    assert sarif["version"] == "2.1.0"
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} >= {"MG001", "MG014"}
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"] == {"startLine": 3}


def test_markdown_table_and_errors():
    report = Report([make_finding(message="a | b")], ["x.sql"], [FileError("y.sql", "bad")])
    out = REPORTERS["markdown"](report)
    assert "| 🔴 | `MG001` drop-table | `db/m1.sql:3` | a \\| b |" in out
    assert "`y.sql`: bad" in out


def test_errors_are_reported_in_text_and_github():
    report = Report([], [], [FileError("y.sql", "unterminated string")])
    assert "error: y.sql: unterminated string" in render_text(report)
    assert "::error file=y.sql::unterminated string" in render_github(report)


def test_failing_threshold(report):
    assert len(failing(report.findings, Severity.ERROR)) == 1
    assert len(failing(report.findings, Severity.WARNING)) == 2
