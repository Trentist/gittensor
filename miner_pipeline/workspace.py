"""Clone / worktree helpers for PR workspaces."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from miner_pipeline.config import PipelineConfig


class WorkspaceError(RuntimeError):
    pass


@dataclass
class Workspace:
    repo: str
    clone_dir: Path
    worktree_dir: Optional[Path] = None
    branch: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            'repo': self.repo,
            'clone_dir': str(self.clone_dir),
            'worktree_dir': str(self.worktree_dir) if self.worktree_dir else None,
            'branch': self.branch,
        }


def _run(cmd: list[str], cwd: Optional[Path] = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise WorkspaceError(f'{" ".join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}')
    return proc.stdout.strip()


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '-', text.lower()).strip('-')
    return (s or 'work')[:max_len]


def ensure_clone(repo: str, cfg: PipelineConfig, token: Optional[str] = None) -> Path:
    """Shallow clone or fetch an existing clone under work_dir/clones."""
    cfg.ensure_dirs()
    owner, _, name = repo.partition('/')
    dest = cfg.clones_dir / owner / name
    if dest.is_dir() and (dest / '.git').exists():
        _run(['git', 'fetch', '--depth', str(cfg.clone_depth), 'origin'], cwd=dest)
        # Prefer default branch tip
        try:
            _run(['git', 'checkout', 'main'], cwd=dest)
        except WorkspaceError:
            try:
                _run(['git', 'checkout', 'master'], cwd=dest)
            except WorkspaceError:
                pass
        try:
            _run(['git', 'pull', '--ff-only'], cwd=dest)
        except WorkspaceError:
            pass
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    if token:
        url = f'https://x-access-token:{token}@github.com/{repo}.git'
    else:
        url = f'https://github.com/{repo}.git'
    _run(['git', 'clone', '--depth', str(cfg.clone_depth), url, str(dest)])
    return dest


def create_worktree(
    repo: str,
    cfg: PipelineConfig,
    *,
    issue_number: Optional[int] = None,
    title: str = 'work',
    token: Optional[str] = None,
) -> Workspace:
    """Create a disposable worktree/branch for implementing a fix."""
    clone_dir = ensure_clone(repo, cfg, token=token)
    branch = f'miner/{"issue-" + str(issue_number) if issue_number else _slug(title)}'
    wt = cfg.worktrees_dir / repo.replace('/', '__') / branch.replace('/', '_')
    if wt.exists():
        return Workspace(repo=repo, clone_dir=clone_dir, worktree_dir=wt, branch=branch)

    wt.parent.mkdir(parents=True, exist_ok=True)
    # Ensure branch does not already exist locally
    existing = _run(['git', 'branch', '--list', branch], cwd=clone_dir)
    if existing:
        _run(['git', 'worktree', 'add', str(wt), branch], cwd=clone_dir)
    else:
        _run(['git', 'worktree', 'add', '-b', branch, str(wt)], cwd=clone_dir)
    return Workspace(repo=repo, clone_dir=clone_dir, worktree_dir=wt, branch=branch)
