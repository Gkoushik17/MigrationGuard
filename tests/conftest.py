import pytest

from migration_guard.analyzer import Analyzer
from migration_guard.config import Config


@pytest.fixture
def analyze():
    """analyze(sql, **config) -> list of rule ids found, in report order."""

    def _run(sql: str, **config_kwargs):
        findings = Analyzer(Config(**config_kwargs)).analyze_text(sql, "m.sql")
        return [f.rule_id for f in findings]

    return _run
