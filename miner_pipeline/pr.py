"""Open PRs with Fixes links under emission preflight gates."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from miner_pipeline.config import PipelineConfig
from miner_pipeline.github_api import GitHubClient, GitHubError
from miner_pipeline.preflight import PreflightResult, preflight_pr
from miner_pipeline.registry import RepoRoute


@dataclass
class PrPlan:
    repo: str
    title: str
    body: str
    head: str
    base: str
    issue_number: Optional[int]
    dry_run: bool
    preflight: PreflightResult

    def to_dict(self) -> dict:
        return {
            'repo': self.repo,
            'title': self.title,
            'body': self.body,
            'head': self.head,
            'base': self.base,
            'issue_number': self.issue_number,
            'dry_run': self.dry_run,
            'preflight': self.preflight.to_dict(),
        }


def build_pr_body(*, summary: str, issue_number: Optional[int], test_plan: str = '- [ ] Local tests') -> str:
    lines = [summary.strip(), '']
    if issue_number is not None:
        lines.append(f'Fixes #{issue_number}')
        lines.append('')
    lines.extend(['## Test plan', test_plan])
    return '\n'.join(lines)


def detect_default_branch(clone_dir: Path) -> str:
    proc = subprocess.run(
        ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
        cwd=clone_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().rsplit('/', 1)[-1]
    for candidate in ('main', 'master'):
        check = subprocess.run(
            ['git', 'rev-parse', '--verify', f'origin/{candidate}'],
            cwd=clone_dir,
            capture_output=True,
            text=True,
        )
        if check.returncode == 0:
            return candidate
    return 'main'


def push_branch(worktree: Path, branch: str, dry_run: bool = True) -> str:
    if dry_run:
        return f'dry-run: would push {branch}'
    proc = subprocess.run(
        ['git', 'push', '-u', 'origin', branch],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or 'git push failed')
    return proc.stdout.strip() or f'pushed {branch}'


def plan_pull_request(
    route: RepoRoute,
    cfg: PipelineConfig,
    *,
    title: str,
    summary: str,
    head: str,
    base: str,
    issue_number: Optional[int],
    open_prs_by_me: int,
    token_score: float = 0.0,
    confirm: bool = False,
) -> PrPlan:
    pf = preflight_pr(
        route,
        open_prs_by_me=open_prs_by_me,
        token_score=token_score,
        reserve=cfg.max_open_prs_reserve,
        has_pat=bool(cfg.github_pat),
        confirm=confirm,
        auto_pr=cfg.auto_pr,
        dry_run=cfg.dry_run,
    )
    body = build_pr_body(summary=summary, issue_number=issue_number)
    return PrPlan(
        repo=route.full_name,
        title=title,
        body=body,
        head=head,
        base=base,
        issue_number=issue_number,
        dry_run=cfg.dry_run and not (confirm or cfg.auto_pr),
        preflight=pf,
    )


def open_pull_request(
    plan: PrPlan,
    cfg: PipelineConfig,
    *,
    confirm: bool = False,
    gh: Optional[GitHubClient] = None,
) -> dict:
    """Open the PR unless dry-run / preflight blocks it."""
    if plan.dry_run and not (confirm or cfg.auto_pr):
        return {'ok': True, 'dry_run': True, 'plan': plan.to_dict()}

    if not plan.preflight.ok:
        return {'ok': False, 'error': 'preflight failed', 'plan': plan.to_dict()}

    gh = gh or GitHubClient(cfg.github_pat)
    owner, _, name = plan.repo.partition('/')
    try:
        created = gh.create_pull(
            owner,
            name,
            title=plan.title,
            body=plan.body,
            head=plan.head,
            base=plan.base,
            draft=False,
        )
    except GitHubError as e:
        return {'ok': False, 'error': str(e), 'plan': plan.to_dict()}

    return {
        'ok': True,
        'dry_run': False,
        'pr_url': created.get('html_url'),
        'pr_number': created.get('number'),
        'plan': plan.to_dict(),
    }
