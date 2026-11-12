"""Command-line entry point.

Exit codes: 0 = no findings at or above --fail-on, 1 = blocking findings,
2 = usage, configuration, git or file errors.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from migration_guard import __version__
from migration_guard.analyzer import Analyzer, discover_files, filter_to_included
from migration_guard.config import DIALECTS, ConfigError, load_config
from migration_guard.gitdiff import GitError, changed_files
from migration_guard.models import Severity
from migration_guard.reporters import REPORTERS, failing, render_text
from migration_guard.rules import all_rules

EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migration-guard",
        description="Flag risky database migrations before they reach review.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check migration files")
    check.add_argument(
        "paths", nargs="*", type=Path, help="files or directories (default: config 'include' globs)"
    )
    check.add_argument("-f", "--format", choices=sorted(REPORTERS), default="text")
    check.add_argument(
        "--fail-on",
        choices=[str(s) for s in Severity],
        help="lowest severity that fails the run (default: error)",
    )
    check.add_argument("--dialect", choices=DIALECTS)
    check.add_argument("--config", type=Path, help="path to a TOML config file")
    check.add_argument(
        "--changed-since",
        metavar="REF",
        help="only check migrations changed since this git ref (e.g. origin/main)",
    )
    check.add_argument("-o", "--output", type=Path, help="write the report to a file")
    check.add_argument(
        "--summary-file",
        type=Path,
        help="also write a markdown summary here (e.g. $GITHUB_STEP_SUMMARY)",
    )
    check.add_argument("--no-color", action="store_true")

    rules = sub.add_parser("rules", help="list available rules")
    rules.add_argument("--dialect", choices=DIALECTS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "rules":
        return _cmd_rules(args)
    return _cmd_check(args)


def _cmd_rules(args: argparse.Namespace) -> int:
    for rule in all_rules():
        if args.dialect and not rule.applies_to(args.dialect):
            continue
        scope = ",".join(sorted(rule.dialects)) if rule.dialects else "all"
        print(f"{rule.id}  {str(rule.severity):<7}  {rule.name:<38} [{scope}]")
        print(f"       {rule.summary}")
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    root = Path.cwd()
    try:
        config = load_config(args.config, root).with_overrides(
            dialect=args.dialect,
            fail_on=Severity.parse(args.fail_on) if args.fail_on else None,
        )
        if args.changed_since:
            candidates = changed_files(args.changed_since, root)
            if args.paths:
                wanted = [p.resolve() for p in args.paths]
                candidates = [
                    c
                    for c in candidates
                    if any(c.resolve() == w or w in c.resolve().parents for w in wanted)
                ]
            files = filter_to_included(candidates, config, root)
        else:
            files = discover_files(args.paths, config, root)
    except (ConfigError, GitError, FileNotFoundError) as exc:
        print(f"migration-guard: error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = Analyzer(config).analyze_files(files, root)

    if args.format == "text":
        use_color = (
            not args.no_color
            and args.output is None
            and sys.stdout.isatty()
            and "NO_COLOR" not in os.environ
        )
        output = render_text(report, color=use_color)
    else:
        output = REPORTERS[args.format](report)

    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    if args.summary_file:
        with args.summary_file.open("a", encoding="utf-8") as fh:
            fh.write(REPORTERS["markdown"](report) + "\n")

    if report.errors:
        return EXIT_ERROR
    return EXIT_FINDINGS if failing(report.findings, config.fail_on) else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
