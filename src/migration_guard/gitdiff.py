"""Find migration files changed on the current branch, so CI only checks new work."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def changed_files(base_ref: str, repo: Path) -> list[Path]:
    """Files added, copied, modified or renamed between ``base_ref`` and HEAD.

    Uses the three-dot form so only changes made on this branch are returned,
    not changes that landed on the base branch after we branched off.
    """
    cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...HEAD"]
    try:
        result = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, check=False, timeout=60
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git diff timed out") from exc
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git diff failed with code {result.returncode}")
    return [repo / line for line in result.stdout.splitlines() if line.strip()]
