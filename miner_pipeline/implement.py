"""Assisted implementation hooks (patch apply + test commands)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ImplementResult:
    ok: bool
    steps: List[str]
    message: str

    def to_dict(self) -> dict:
        return {'ok': self.ok, 'steps': self.steps, 'message': self.message}


def apply_patch(worktree: Path, patch_file: Path) -> ImplementResult:
    """Apply a unified diff inside a worktree."""
    if not patch_file.is_file():
        return ImplementResult(ok=False, steps=[], message=f'patch not found: {patch_file}')
    proc = subprocess.run(
        ['git', 'apply', '--index', str(patch_file)],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ImplementResult(
            ok=False,
            steps=[f'git apply {patch_file}'],
            message=proc.stderr.strip() or proc.stdout.strip() or 'git apply failed',
        )
    return ImplementResult(ok=True, steps=[f'git apply {patch_file}'], message='patch applied')


def run_tests(worktree: Path, command: Optional[List[str]] = None) -> ImplementResult:
    """Run a test command in the worktree (default: detect common runners)."""
    if command is None:
        if (worktree / 'pyproject.toml').exists():
            command = ['python', '-m', 'pytest', '-q']
        elif (worktree / 'Cargo.toml').exists():
            command = ['cargo', 'test', '--quiet']
        elif (worktree / 'package.json').exists():
            command = ['npm', 'test', '--silent']
        else:
            return ImplementResult(
                ok=True,
                steps=[],
                message='no default test runner detected; skip or pass --test-cmd',
            )

    proc = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
    step = ' '.join(command)
    if proc.returncode != 0:
        return ImplementResult(
            ok=False,
            steps=[step],
            message=(proc.stderr or proc.stdout)[-2000:] or 'tests failed',
        )
    return ImplementResult(ok=True, steps=[step], message='tests passed')


def commit_all(worktree: Path, message: str) -> ImplementResult:
    """Stage and commit all changes in the worktree."""
    steps = ['git add -A', f'git commit -m {message!r}']
    subprocess.run(['git', 'add', '-A'], cwd=worktree, check=False)
    proc = subprocess.run(
        ['git', 'commit', '-m', message],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        out = (proc.stdout + proc.stderr).strip()
        if 'nothing to commit' in out.lower():
            return ImplementResult(ok=False, steps=steps, message='nothing to commit')
        return ImplementResult(ok=False, steps=steps, message=out[-1000:])
    return ImplementResult(ok=True, steps=steps, message='committed')
