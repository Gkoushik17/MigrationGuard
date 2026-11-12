import pytest

from migration_guard.rules import all_rules
from migration_guard.rules.builtin import split_top_level

LT = "SET lock_timeout = '5s'; "  # silences MG012 so each case tests one rule


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("DROP TABLE users;", ["MG001", "MG012"]),
        (LT + "drop table if exists public.users, sessions;", ["MG001"]),
        (LT + "ALTER TABLE users DROP COLUMN age;", ["MG002"]),
        (LT + "ALTER TABLE users DROP age;", ["MG002"]),
        (LT + "ALTER TABLE users ALTER COLUMN age TYPE BIGINT;", ["MG003"]),
        (LT + "ALTER TABLE users ALTER age SET DATA TYPE BIGINT;", ["MG003"]),
        (LT + "ALTER TABLE users ADD COLUMN tier TEXT NOT NULL;", ["MG004"]),
        ("CREATE INDEX idx ON users (email);", ["MG005"]),
        ("CREATE UNIQUE INDEX IF NOT EXISTS idx ON users (email);", ["MG005"]),
        ("CREATE INDEX ON users (email);", ["MG005"]),
        (LT + "ALTER TABLE users RENAME COLUMN a TO b;", ["MG006"]),
        (LT + "ALTER TABLE users RENAME TO people;", ["MG006"]),
        ("TRUNCATE TABLE users;", ["MG007", "MG012"]),
        ("UPDATE users SET active = true;", ["MG008"]),
        ("DELETE FROM users;", ["MG008"]),
        (
            LT + "ALTER TABLE o ADD CONSTRAINT fk FOREIGN KEY (u) REFERENCES users (id);",
            ["MG009"],
        ),
        (LT + "ALTER TABLE users ALTER COLUMN email SET NOT NULL;", ["MG010"]),
        (LT + "ALTER TABLE users ADD CONSTRAINT c CHECK (age > 0);", ["MG011"]),
        ("ALTER TABLE users ADD COLUMN nickname TEXT;", ["MG012"]),
        ("DROP INDEX idx_users_email;", ["MG013"]),
        (LT + "ALTER TABLE users ADD CONSTRAINT u UNIQUE (email);", ["MG014"]),
        (LT + "ALTER TABLE users ADD PRIMARY KEY (id);", ["MG014"]),
    ],
)
def test_rule_detects_risky_sql(analyze, sql, expected):
    assert sorted(analyze(sql)) == sorted(expected)


@pytest.mark.parametrize(
    "sql",
    [
        LT + "ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free' NOT NULL;",
        "SET LOCAL lock_timeout = '5s'; ALTER TABLE users ADD COLUMN nickname TEXT;",
        LT + "ALTER TABLE users ADD COLUMN id2 BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL;",
        "CREATE INDEX CONCURRENTLY idx ON users (email);",
        "DROP INDEX CONCURRENTLY IF EXISTS idx;",
        "UPDATE users SET active = true WHERE id = 1;",
        LT + "ALTER TABLE users ALTER COLUMN email DROP NOT NULL;",
        LT + "ALTER TABLE users DROP CONSTRAINT users_email_key;",
        LT + "ALTER TABLE o ADD CONSTRAINT fk FOREIGN KEY (u) REFERENCES users (id) NOT VALID;",
        LT + "ALTER TABLE users ADD CONSTRAINT c CHECK (age > 0) NOT VALID;",
        LT + "ALTER TABLE users ADD CONSTRAINT u UNIQUE USING INDEX idx_u;",
        "INSERT INTO audit (note) VALUES ('DROP TABLE users; TRUNCATE x');",
        "CREATE TABLE IF NOT EXISTS events (id BIGINT PRIMARY KEY, kind TEXT NOT NULL);",
    ],
)
def test_safe_sql_has_no_findings(analyze, sql):
    assert analyze(sql) == []


def test_operations_on_tables_created_in_same_migration_are_allowed(analyze):
    sql = """
    CREATE TABLE "Public"."Events" (id BIGINT);
    CREATE INDEX idx_events ON events (id);
    ALTER TABLE public.events ADD COLUMN kind TEXT NOT NULL;
    ALTER TABLE events ALTER COLUMN id SET NOT NULL;
    """
    assert analyze(sql) == []


def test_drop_column_is_flagged_even_on_new_tables(analyze):
    # Dropping is never part of a correct "create" migration, so stay loud.
    assert "MG002" in analyze("CREATE TABLE t (a INT); ALTER TABLE t DROP COLUMN a;")


def test_multiple_actions_in_one_alter_are_each_checked(analyze):
    sql = (
        LT + "ALTER TABLE users ADD COLUMN price NUMERIC(10, 2) NOT NULL, "
        "DROP COLUMN legacy, ALTER COLUMN age TYPE BIGINT;"
    )
    assert sorted(analyze(sql)) == ["MG002", "MG003", "MG004"]


def test_missing_lock_timeout_reported_once_per_file(analyze):
    sql = "ALTER TABLE a ADD COLUMN x INT; ALTER TABLE b ADD COLUMN y INT;"
    assert analyze(sql) == ["MG012"]


def test_lock_timeout_set_after_the_ddl_does_not_count(analyze):
    assert analyze("ALTER TABLE a ADD COLUMN x INT; " + LT) == ["MG012"]


def test_postgres_only_rules_are_skipped_for_mysql(analyze):
    sql = "CREATE INDEX idx ON users (email); ALTER TABLE users MODIFY COLUMN age BIGINT;"
    assert analyze(sql, dialect="mysql") == ["MG003"]
    assert analyze("RENAME TABLE a TO b;", dialect="mysql") == ["MG006"]


def test_disabled_rules_and_suppressions(analyze):
    assert analyze(LT + "DROP TABLE a;", disable=frozenset({"MG001"})) == []
    assert analyze(LT + "DROP TABLE a; -- migration-guard: ignore MG001") == []
    assert analyze("-- migration-guard: ignore-file\nDROP TABLE a;") == []


def test_split_top_level_respects_parentheses():
    assert split_top_level("a NUMERIC(10,2), b INT, c CHECK (x IN (1,2))") == [
        "a NUMERIC(10,2)",
        "b INT",
        "c CHECK (x IN (1,2))",
    ]


def test_every_rule_has_metadata_and_unique_id():
    rules = all_rules()
    assert len({r.id for r in rules}) == len(rules)
    for rule in rules:
        assert rule.id.startswith("MG") and rule.name and rule.summary
