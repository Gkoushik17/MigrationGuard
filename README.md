# MigrationGuard

A CI check that flags risky database migrations before they reach review.

```text
$ migration-guard check examples/migrations

examples/migrations/risky/0007_orders_cleanup.sql
     2  error    MG004 add-not-null-column-without-default
        Column orders.customer_tier is NOT NULL without a DEFAULT; the migration fails as soon as the table has rows.
        fix: Add the column as nullable (or with a constant DEFAULT), backfill it, then add the NOT NULL constraint in a follow-up migration.
     4  error    MG003 alter-column-type
        Changing the type of orders.amount can rewrite the whole table while blocking reads and writes.
        fix: Add a new column with the target type, backfill it in batches, switch the application over, then drop the old column.
     6  warning  MG005 create-index-non-concurrently
        Building an index on orders without CONCURRENTLY blocks all writes to the table until the build finishes.
        fix: Use CREATE INDEX CONCURRENTLY and run it outside a transaction block.
   ...

5 errors, 4 warnings in 2 files scanned.
```

A migration that reads as one line in a diff can lock a production table for minutes
or break the old app version during a rolling deploy. MigrationGuard catches these
problems statically, with no database connection. It explains each one and suggests a
safe alternative, inline on the pull request.

## Features

- **14 rules** covering table rewrites, write-blocking locks, destructive changes and
  rolling-deploy hazards (see below)
- **Few false positives:** a real SQL lexer ignores strings, comments and `$$` bodies.
  Tables created in the same migration are treated as empty, so they are not flagged.
- **Only checks what changed:** `--changed-since origin/main` limits the check to the PR's migrations
- **Five output formats:** `text`, `json`, `github` (inline PR annotations), `sarif`
  (GitHub code scanning) and `markdown` (job summary)
- **Configurable:** disable rules, change their severity, set the failure threshold,
  or suppress one statement with a reviewed `-- migration-guard: ignore MG008`
- **Ships as** a CLI, a GitHub Action, a Docker image and a pre-commit hook

## Quick start

```bash
pip install .
migration-guard check db/migrations
migration-guard check --changed-since origin/main --format github
migration-guard rules             # list every rule
```

Exit codes: `0` no blocking findings, `1` findings at or above `--fail-on`
(default `error`), `2` usage, config, git or parse error.

### GitHub Action

```yaml
# .github/workflows/migrations.yml
on: pull_request
jobs:
  migration-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: Gkoushik17/MigrationGuard@v0.1.0
        with:
          fail-on: error
```

Findings appear as annotations on the changed lines and as a table in the job summary.

### Docker

```bash
docker build -t migration-guard .
docker run --rm -v "$PWD:/repo" migration-guard check db/migrations
```

### pre-commit

```yaml
repos:
  - repo: https://github.com/Gkoushik17/MigrationGuard
    rev: v0.1.0
    hooks: [{ id: migration-guard }]
```

## Rules

| ID | Name | Severity | Why it is risky |
|---|---|---|---|
| MG001 | drop-table | error | Irreversible; breaks code still reading the table |
| MG002 | drop-column | error | Old app instances still select it during a rolling deploy |
| MG003 | alter-column-type | error | Usually rewrites the table under an exclusive lock |
| MG004 | add-not-null-column-without-default | error | Fails on any non-empty table |
| MG005 | create-index-non-concurrently | warning | Blocks writes for the whole index build (Postgres) |
| MG006 | rename | warning | Old app instances use the old name during deploy |
| MG007 | truncate | error | Deletes every row under an ACCESS EXCLUSIVE lock |
| MG008 | unbounded-update-or-delete | error | Touches every row in one long transaction |
| MG009 | foreign-key-without-not-valid | warning | Validates all rows while blocking writes (Postgres) |
| MG010 | set-not-null | warning | Full table scan under ACCESS EXCLUSIVE (Postgres) |
| MG011 | check-constraint-without-not-valid | warning | Full table scan under lock (Postgres) |
| MG012 | missing-lock-timeout | warning | Lock-taking DDL can queue and stall all traffic (Postgres) |
| MG013 | drop-index-non-concurrently | warning | Exclusive lock on the table (Postgres) |
| MG014 | add-unique-constraint-without-index | warning | Builds an index under a write lock (Postgres) |

## Configuration

`.migration-guard.toml`, or `[tool.migration-guard]` in `pyproject.toml`:

```toml
dialect = "postgres"                 # postgres | mysql | generic
include = ["db/migrations/**/*.sql"]
exclude = ["db/migrations/legacy/**"]
fail_on = "error"                    # info | warning | error
disable = ["MG006"]

[severity]
MG005 = "error"                      # make non-concurrent index builds blocking here
```

Suppress a reviewed statement inline:

```sql
-- Approved by data team, ticket DATA-412
-- migration-guard: ignore MG008
DELETE FROM feature_flags;
```

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
pytest --cov            # 96 tests, ~97% branch coverage, 90% gate
```

The architecture, trade-offs and test strategy are covered in
[docs/design.md](docs/design.md). To add a rule, subclass `Rule` (or `AlterTableRule`),
decorate it with `@register`, and add positive and negative cases to
`tests/test_rules.py`.

## License

MIT
