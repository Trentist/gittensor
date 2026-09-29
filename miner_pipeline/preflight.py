"""Emission and spam preflight gates before opening PRs or filing issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from miner_pipeline.config import PipelineConfig
from miner_pipeline.registry import RepoRoute


@dataclass
class PreflightResult:
    ok: bool
    route: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'route': self.route,
            'reasons': self.reasons,
            'warnings': self.warnings,
        }


def preflight_pr(
    route: RepoRoute,
    *,
    open_prs_by_me: int,
    token_score: float = 0.0,
    reserve: int = 1,
    has_pat: bool = True,
    confirm: bool = False,
    auto_pr: bool = False,
    dry_run: bool = True,
) -> PreflightResult:
    """Gate opening a new PR on this repo."""
    reasons: List[str] = []
    warnings: List[str] = []

    if route.pr_pool_fraction <= 0:
        reasons.append('PR pool fraction is 0 for this repo (emission_share or discovery share leaves nothing for PRs)')

    if not has_pat:
        reasons.append('GitHub PAT missing (set GITTENSOR_MINER_PAT or GITHUB_TOKEN)')

    threshold = route.open_pr_threshold(token_score)
    # After opening one more PR, count would be open_prs_by_me + 1
    projected = open_prs_by_me + 1
    # Keep `reserve` slots free under threshold when possible
    effective_cap = max(0, threshold - reserve)
    if projected > threshold:
        reasons.append(
            f'open PR spam risk: have {open_prs_by_me} open, threshold={threshold}; opening another zeros merged score'
        )
    elif projected > effective_cap:
        warnings.append(
            f'approaching open-PR threshold ({open_prs_by_me}/{threshold}); reserve={reserve}'
        )

    if route.unlabeled_is_dead:
        warnings.append(
            'default_label_multiplier is 0 — unlabeled merged PRs score ~0; chase trusted eval/crown labels'
        )

    if dry_run and not (confirm or auto_pr):
        warnings.append('dry_run=True: will not open PR unless --confirm or AUTO_PR')

    if not confirm and not auto_pr and not dry_run:
        reasons.append('refusing to open PR without --confirm or auto_pr=True')

    ok = not reasons
    return PreflightResult(ok=ok, route='pr', reasons=reasons, warnings=warnings)


def preflight_discovery(
    route: RepoRoute,
    *,
    open_issues_by_me: int,
    solved_token_score: float = 0.0,
    has_pat: bool = True,
    confirm: bool = False,
    auto_file: bool = False,
    dry_run: bool = True,
) -> PreflightResult:
    """Gate filing a discovery issue on this repo."""
    reasons: List[str] = []
    warnings: List[str] = []

    if not route.discovery_enabled:
        reasons.append(
            f'issue_discovery_share={route.issue_discovery_share} — discovery emissions disabled; draft only'
        )

    if not has_pat:
        reasons.append('GitHub PAT missing')

    projected_open = open_issues_by_me + 1
    if not route.open_issue_allowed(projected_open, solved_token_score):
        reasons.append(
            f'open-issue spam: projecting {projected_open} open issues would zero discovery score for this repo'
        )

    if dry_run and not (confirm or auto_file):
        warnings.append('dry_run=True: will not file issue unless --confirm or AUTO_FILE')

    if not confirm and not auto_file and not dry_run:
        reasons.append('refusing to file issue without --confirm or auto_file_issues=True')

    ok = not reasons
    return PreflightResult(ok=ok, route='discovery', reasons=reasons, warnings=warnings)


def assert_config_ready(cfg: PipelineConfig) -> Optional[str]:
    """Return error message if config cannot run read-only sync."""
    return None
