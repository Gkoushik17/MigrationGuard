import pytest

from migration_guard.config import Config, ConfigError, load_config
from migration_guard.models import Severity


def test_defaults_when_no_config_present(tmp_path):
    assert load_config(cwd=tmp_path) == Config()


def test_dedicated_file_takes_priority_over_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.migration-guard]\ndialect = "mysql"\n')
    (tmp_path / ".migration-guard.toml").write_text('dialect = "generic"\n')
    assert load_config(cwd=tmp_path).dialect == "generic"


def test_reads_pyproject_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.migration-guard]\nfail_on = "warning"\ndisable = ["mg006"]\n'
        '[tool.migration-guard.severity]\nMG005 = "error"\n'
    )
    config = load_config(cwd=tmp_path)
    assert config.fail_on is Severity.WARNING
    assert config.disable == {"MG006"}
    assert config.severity_overrides == {"MG005": Severity.ERROR}


def test_pyproject_without_section_uses_defaults(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert load_config(cwd=tmp_path) == Config()


def test_explicit_path(tmp_path):
    path = tmp_path / "custom.toml"
    path.write_text('include = ["db/*.sql"]\n')
    assert load_config(path, cwd=tmp_path).include == ("db/*.sql",)


def test_explicit_pyproject_path(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.migration-guard]\ndialect = "mysql"\n')
    assert load_config(path, cwd=tmp_path).dialect == "mysql"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('dialect = "oracle"', "dialect"),
        ('disable = ["MG999"]', "unknown rule"),
        ('fail_on = "fatal"', "unknown severity"),
        ("colour = true", "unknown key"),
        ('[severity]\nMG999 = "error"', "unknown rule"),
        ('[severity]\nMG001 = "loud"', "unknown severity"),
        ("not = = toml", "invalid TOML"),
    ],
)
def test_invalid_config_is_rejected_with_clear_message(tmp_path, content, message):
    (tmp_path / ".migration-guard.toml").write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_config(cwd=tmp_path)


def test_missing_explicit_config(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", cwd=tmp_path)


def test_with_overrides_ignores_none():
    config = Config().with_overrides(dialect=None, fail_on=Severity.INFO)
    assert config.dialect == "postgres" and config.fail_on is Severity.INFO
