import pytest

from migration_guard.parser import SqlParseError, parse_sql


def test_splits_statements_and_tracks_lines():
    parsed = parse_sql("SELECT 1;\n\nSELECT\n  2;\n")
    assert [s.normalized for s in parsed.statements] == ["SELECT 1", "SELECT 2"]
    assert [(s.start_line, s.end_line) for s in parsed.statements] == [(1, 1), (3, 4)]


def test_semicolons_inside_strings_comments_and_dollar_quotes_do_not_split():
    sql = """
    INSERT INTO t VALUES ('a;b', 'it''s; fine'); -- trailing; comment
    /* block; comment */
    CREATE FUNCTION f() RETURNS void AS $body$ BEGIN DELETE FROM x; END; $body$ LANGUAGE plpgsql;
    DO $$ BEGIN UPDATE y SET z = 1; END $$;
    """
    statements = parse_sql(sql).statements
    assert len(statements) == 3
    assert "'?'" in statements[0].normalized and "a;b" not in statements[0].normalized


def test_line_numbers_survive_multiline_strings_and_comments():
    sql = "/* one\ntwo */\nINSERT INTO t VALUES ('a\nb');\nDROP TABLE x;"
    first, second = parse_sql(sql).statements
    assert (first.start_line, first.end_line) == (3, 4)
    assert second.start_line == 5


def test_string_contents_are_masked_in_normalized_form():
    stmt = parse_sql("INSERT INTO notes VALUES ('drop table users');").statements[0]
    assert "drop table" not in stmt.normalized.lower()
    assert "drop table" in stmt.text.lower()


def test_trailing_statement_without_semicolon():
    statements = parse_sql("SELECT 1;\nDROP TABLE x").statements
    assert statements[-1].normalized == "DROP TABLE x"


def test_comment_only_input_has_no_statements():
    assert parse_sql("-- nothing\n/* here */\n").statements == []


@pytest.mark.parametrize(
    "sql", ["SELECT 'oops;", 'SELECT "oops;', "/* never closed", "SELECT $$ open"]
)
def test_unterminated_tokens_raise(sql):
    with pytest.raises(SqlParseError):
        parse_sql(sql)


def test_own_line_directive_applies_to_next_statement_only():
    sql = "-- migration-guard: ignore MG001, mg002\nDROP TABLE a;\nDROP TABLE b;"
    first, second = parse_sql(sql).statements
    assert first.suppressed == {"MG001", "MG002"}
    assert second.suppressed == frozenset()


def test_trailing_directive_applies_to_same_line_statement():
    sql = "DROP TABLE a; -- migration-guard: ignore\nDROP TABLE b;"
    first, second = parse_sql(sql).statements
    assert first.is_suppressed("MG001") and first.is_suppressed("MG999")
    assert not second.is_suppressed("MG001")


def test_ignore_file_directive():
    assert parse_sql("/* migration-guard: ignore-file */ DROP TABLE a;").ignore_file
