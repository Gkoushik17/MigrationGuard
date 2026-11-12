import json
import subprocess
from pathlib import Path

import pytest

from migration_guard import cli

RISKY = "ALTER TABLE users DROP COLUMN age;\n"
WARN_ONLY = "SET lock_timeout = '5s';\nCREATE INDEX idx ON users (email);\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "db" / "migrations").mkdir(parents=True)
    return tmp_path


def write(repo: Path, name: str, sql: str) -> Path:
    path = repo / "db" / "migrations" / name
    path.write_text(sql)
    return path


def test_exit_codes_follow_fail_on(repo, capsys):
    write(repo, "001.sql", WARN_ONLY)
    assert cli.main(["check"]) == 0
    assert cli.main(["check", "--fail-on", "warning"]) == 1
    assert "MG005" in capsys.readouterr().out


def test_blocking_finding_fails(repo, capsys):
    write(repo, "001.sql", RISKY)
    assert cli.main(["check", "db"]) == 1
    assert "MG002 drop-column" in capsys.readouterr().out


def test_json_output_to_file_and_summary_file(repo):
    write(repo, "001.sql", RISKY)
    out, summary = repo / "report.json", repo / "summary.md"
    code = cli.main(["check", "-f", "json", "-o", str(out), "--summary-file", str(summary)])
    assert code == 1
    assert json.loads(out.read_text())["findings"][0]["file"] == "db/migrations/001.sql"
    assert "### MigrationGuard" in summary.read_text()


def test_exclude_globs(repo):
    write(repo, "001.sql", RISKY)
    (repo / ".migration-guard.toml").write_text('exclude = ["db/migrations/001.sql"]\n')
    assert cli.main(["check"]) == 0


def test_unparseable_file_is_an_error(repo, capsys):
    write(repo, "001.sql", "SELECT 'unterminated;")
    assert cli.main(["check"]) == 2
    assert "unterminated string" in capsys.readouterr().out


def test_missing_path_and_bad_config_exit_2(repo, capsys):
    assert cli.main(["check", "nope.sql"]) == 2
    (repo / ".migration-guard.toml").write_text('dialect = "oracle"\n')
    assert cli.main(["check"]) == 2
    assert "dialect" in capsys.readouterr().err


def test_rules_command_lists_and_filters(capsys):
    assert cli.main(["rules"]) == 0
    assert "MG005" in capsys.readouterr().out
    cli.main(["rules", "--dialect", "mysql"])
    out = capsys.readouterr().out
    assert "MG001" in out and "MG005" not in out


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_changed_since_only_checks_branch_changes(repo, capsys):
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    write(repo, "001_old.sql", RISKY)  # already merged: should be ignored
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature")
    write(repo, "002_new.sql", WARN_ONLY)
    (repo / "notes.txt").write_text("not sql")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feature")

    assert cli.main(["check", "--changed-since", "main", "--fail-on", "warning"]) == 1
    out = capsys.readouterr().out
    assert "002_new.sql" in out and "001_old.sql" not in out
    assert "1 file scanned" in out

    assert cli.main(["check", "--changed-since", "main", "db/migrations/001_old.sql"]) == 0


def test_changed_since_bad_ref(repo, capsys):
    git(repo, "init", "-q")
    assert cli.main(["check", "--changed-since", "does-not-exist"]) == 2
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("rel", "pattern", "expected"),
    [
        ("db/migrations/001.sql", "**/migrations/**/*.sql", True),
        ("migrations/001.sql", "**/migrations/**/*.sql", True),
        ("a/b/migrations/x/y/001.sql", "**/migrations/**/*.sql", True),
        ("db/migrations_old/001.sql", "**/migrations/**/*.sql", False),
        ("db/001.sql", "db/*.sql", True),
        ("db/sub/001.sql", "db/*.sql", False),
        ("db/00?.sql", "db/00?.sql", True),
    ],
)
def test_glob_matching(rel, pattern, expected):
    from migration_guard.analyzer import _glob_match

    assert _glob_match(rel, pattern) is expected
