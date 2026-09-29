"""Issue discovery drafts and gated filing (only when share > 0)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from miner_pipeline.config import PipelineConfig
from miner_pipeline.github_api import GitHubClient, GitHubError
from miner_pipeline.preflight import preflight_discovery
from miner_pipeline.registry import RepoRoute


@dataclass
class IssueDraft:
    repo: str
    title: str
    body: str
    source: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def render_issue_body(*, problem: str, repro: str, expected: str, acceptance: str) -> str:
    return '\n'.join(
        [
            '## Problem',
            problem.strip(),
            '',
            '## Repro',
            repro.strip() or '_TBD_',
            '',
            '## Expected',
            expected.strip() or '_TBD_',
            '',
            '## Acceptance',
            acceptance.strip() or '- [ ] Fix merged with tests',
            '',
            '---',
            '_Drafted by gittensor miner_pipeline. Prefer non-self solves for discovery score._',
        ]
    )


def save_draft(cfg: PipelineConfig, draft: IssueDraft) -> Path:
    cfg.ensure_dirs()
    safe_repo = draft.repo.replace('/', '__')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = cfg.drafts_dir / f'{safe_repo}_{stamp}.json'
    path.write_text(json.dumps(draft.to_dict(), indent=2), encoding='utf-8')
    md = path.with_suffix('.md')
    md.write_text(f'# {draft.title}\n\n{draft.body}\n', encoding='utf-8')
    return path


def draft_from_todo(
    route: RepoRoute,
    *,
    file_path: str,
    line: int,
    text: str,
) -> IssueDraft:
    body = render_issue_body(
        problem=f'Code marker in `{file_path}:{line}`: {text}',
        repro=f'Inspect `{file_path}` around line {line}.',
        expected='Marker resolved with a tested fix.',
        acceptance='- [ ] TODO/FIXME removed or converted to tracked work\n- [ ] Tests cover the behavior',
    )
    return IssueDraft(
        repo=route.full_name,
        title=f'{file_path}:{line} — {text[:80]}',
        body=body,
        source='todo-scan',
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def file_issue(
    route: RepoRoute,
    cfg: PipelineConfig,
    *,
    title: str,
    body: str,
    open_issues_by_me: int,
    solved_token_score: float = 0.0,
    confirm: bool = False,
    gh: Optional[GitHubClient] = None,
) -> dict:
    pf = preflight_discovery(
        route,
        open_issues_by_me=open_issues_by_me,
        solved_token_score=solved_token_score,
        has_pat=bool(cfg.github_pat),
        confirm=confirm,
        auto_file=cfg.auto_file_issues,
        dry_run=cfg.dry_run,
    )
    draft = IssueDraft(
        repo=route.full_name,
        title=title,
        body=body,
        source='manual',
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    draft_path = save_draft(cfg, draft)

    if not route.discovery_enabled:
        return {
            'ok': False,
            'filed': False,
            'draft_path': str(draft_path),
            'preflight': pf.to_dict(),
            'error': 'discovery disabled on this repo; draft saved only',
        }

    if cfg.dry_run and not (confirm or cfg.auto_file_issues):
        return {
            'ok': True,
            'filed': False,
            'dry_run': True,
            'draft_path': str(draft_path),
            'preflight': pf.to_dict(),
        }

    if not pf.ok:
        return {
            'ok': False,
            'filed': False,
            'draft_path': str(draft_path),
            'preflight': pf.to_dict(),
            'error': 'preflight failed',
        }

    gh = gh or GitHubClient(cfg.github_pat)
    owner, _, name = route.full_name.partition('/')
    try:
        created = gh.create_issue(owner, name, title=title, body=body)
    except GitHubError as e:
        return {
            'ok': False,
            'filed': False,
            'draft_path': str(draft_path),
            'preflight': pf.to_dict(),
            'error': str(e),
        }

    return {
        'ok': True,
        'filed': True,
        'issue_url': created.get('html_url'),
        'issue_number': created.get('number'),
        'draft_path': str(draft_path),
        'preflight': pf.to_dict(),
    }


def list_drafts(cfg: PipelineConfig) -> List[dict]:
    cfg.ensure_dirs()
    out = []
    for path in sorted(cfg.drafts_dir.glob('*.json')):
        try:
            out.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError):
            continue
    return out
