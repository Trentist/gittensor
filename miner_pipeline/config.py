"""Load operator config for the miner pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_WORK_DIR = Path.home() / '.gittensor' / 'miner_pipeline'
DEFAULT_REGISTRY_REL = Path('gittensor') / 'validator' / 'weights' / 'master_repositories.json'


@dataclass
class PipelineConfig:
    """Runtime config for the miner pipeline."""

    work_dir: Path = field(default_factory=lambda: DEFAULT_WORK_DIR)
    github_pat_env: str = 'GITTENSOR_MINER_PAT'
    dry_run: bool = True
    auto_pr: bool = False
    auto_file_issues: bool = False
    repo_allowlist: List[str] = field(default_factory=list)
    max_open_prs_reserve: int = 1
    max_opportunities: int = 25
    clone_depth: int = 50
    todo_globs: List[str] = field(
        default_factory=lambda: [
            '**/*.py',
            '**/*.rs',
            '**/*.cu',
            '**/*.cpp',
            '**/*.c',
            '**/*.h',
            '**/*.ts',
            '**/*.js',
            '**/*.go',
        ]
    )
    skip_path_parts: List[str] = field(
        default_factory=lambda: ['.git', 'node_modules', 'target', 'dist', '__pycache__', '.venv']
    )

    @property
    def github_pat(self) -> Optional[str]:
        return os.environ.get(self.github_pat_env) or os.environ.get('GITHUB_TOKEN')

    @property
    def state_dir(self) -> Path:
        return self.work_dir / 'state'

    @property
    def drafts_dir(self) -> Path:
        return self.work_dir / 'drafts'

    @property
    def clones_dir(self) -> Path:
        return self.work_dir / 'clones'

    @property
    def worktrees_dir(self) -> Path:
        return self.work_dir / 'worktrees'

    def ensure_dirs(self) -> None:
        for path in (self.work_dir, self.state_dir, self.drafts_dir, self.clones_dir, self.worktrees_dir):
            path.mkdir(parents=True, exist_ok=True)


def _as_path(value: Any, default: Path) -> Path:
    if value is None:
        return default
    return Path(os.path.expanduser(str(value))).resolve()


def load_config(path: Optional[Path] = None) -> PipelineConfig:
    """Load YAML config if present; otherwise return defaults."""
    cfg = PipelineConfig()
    if path is None:
        candidates = [
            Path.cwd() / 'miner_pipeline.yaml',
            Path.cwd() / 'miner_pipeline' / 'config.yaml',
            DEFAULT_WORK_DIR / 'config.yaml',
        ]
        path = next((p for p in candidates if p.is_file()), None)

    if path is None:
        return cfg

    with open(path, encoding='utf-8') as f:
        raw: Dict[str, Any] = yaml.safe_load(f) or {}

    cfg.work_dir = _as_path(raw.get('work_dir'), cfg.work_dir)
    cfg.github_pat_env = str(raw.get('github_pat_env', cfg.github_pat_env))
    cfg.dry_run = bool(raw.get('dry_run', cfg.dry_run))
    cfg.auto_pr = bool(raw.get('auto_pr', cfg.auto_pr))
    cfg.auto_file_issues = bool(raw.get('auto_file_issues', cfg.auto_file_issues))
    cfg.repo_allowlist = [str(r).lower() for r in raw.get('repo_allowlist', [])]
    cfg.max_open_prs_reserve = int(raw.get('max_open_prs_reserve', cfg.max_open_prs_reserve))
    cfg.max_opportunities = int(raw.get('max_opportunities', cfg.max_opportunities))
    cfg.clone_depth = int(raw.get('clone_depth', cfg.clone_depth))
    if raw.get('todo_globs'):
        cfg.todo_globs = [str(g) for g in raw['todo_globs']]
    if raw.get('skip_path_parts'):
        cfg.skip_path_parts = [str(p) for p in raw['skip_path_parts']]
    return cfg
