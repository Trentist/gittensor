"""Monitor open PRs / issues for label gaps, spam risk, and review pressure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from miner_pipeline.config import PipelineConfig
from miner_pipeline.github_api import GitHubClient, GitHubError
from miner_pipeline.registry import RepoRoute


@dataclass
class RepoHealth:
    repo: str
    my_open_prs: int
    open_pr_threshold: int
    my_open_issues: int
    unlabeled_open_prs: List[str] = field(default_factory=list)
    changes_requested: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'repo': self.repo,
            'my_open_prs': self.my_open_prs,
            'open_pr_threshold': self.open_pr_threshold,
            'my_open_issues': self.my_open_issues,
            'unlabeled_open_prs': self.unlabeled_open_prs,
            'changes_requested': self.changes_requested,
            'warnings': self.warnings,
        }


def _label_names(pr: dict) -> List[str]:
    labels = pr.get('labels') or []
    return [l['name'] for l in labels if isinstance(l, dict) and 'name' in l]


def _matches_trusted_label(names: List[str], route: RepoRoute) -> bool:
    if not route.label_multipliers:
        return True
    import fnmatch

    for name in names:
        for pattern, mult in route.label_multipliers.items():
            if fnmatch.fnmatch(name, pattern) and mult > 0:
                return True
    return False


def monitor_routes(
    routes: List[RepoRoute],
    cfg: PipelineConfig,
    gh: Optional[GitHubClient] = None,
) -> List[RepoHealth]:
    gh = gh or GitHubClient(cfg.github_pat)
    try:
        login = (gh.whoami() or {}).get('login') if cfg.github_pat else None
    except GitHubError:
        login = None

    results: List[RepoHealth] = []
    for route in routes:
        owner, _, name = route.full_name.partition('/')
        warnings: List[str] = []
        try:
            pulls = gh.list_open_pulls(owner, name)
            issues = gh.list_open_issues(owner, name)
        except GitHubError as e:
            results.append(
                RepoHealth(
                    repo=route.full_name,
                    my_open_prs=0,
                    open_pr_threshold=route.open_pr_threshold(0.0),
                    my_open_issues=0,
                    warnings=[str(e)],
                )
            )
            continue

        mine_prs = [p for p in pulls if login and (p.get('user') or {}).get('login') == login]
        mine_issues = [i for i in issues if login and (i.get('user') or {}).get('login') == login]
        threshold = route.open_pr_threshold(0.0)

        unlabeled: List[str] = []
        if route.unlabeled_is_dead:
            for pr in mine_prs:
                names = _label_names(pr)
                if not _matches_trusted_label(names, route):
                    unlabeled.append(pr.get('html_url') or f"#{pr.get('number')}")

        if len(mine_prs) >= threshold:
            warnings.append(f'at/over open-PR spam threshold ({len(mine_prs)}/{threshold})')
        if route.unlabeled_is_dead and unlabeled:
            warnings.append(f'{len(unlabeled)} open PR(s) lack trusted eval/crown labels — score risk after merge')
        if route.discovery_enabled is False:
            warnings.append('discovery share is 0 — do not file issues for discovery emissions')

        results.append(
            RepoHealth(
                repo=route.full_name,
                my_open_prs=len(mine_prs),
                open_pr_threshold=threshold,
                my_open_issues=len(mine_issues),
                unlabeled_open_prs=unlabeled,
                warnings=warnings,
            )
        )
    return results


def health_to_payload(health: List[RepoHealth]) -> Dict[str, Any]:
    return {'repos': [h.to_dict() for h in health]}
