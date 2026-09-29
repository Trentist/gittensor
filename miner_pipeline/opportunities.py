"""Scan master repos for emission-ranked PR opportunities."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from miner_pipeline.config import PipelineConfig
from miner_pipeline.github_api import GitHubClient, GitHubError
from miner_pipeline.registry import RepoRoute

TODO_RE = re.compile(r'\b(TODO|FIXME|XXX|HACK|BUG)\b[:\s].*', re.IGNORECASE)
MAINTAINER_ASSOCS = {'OWNER', 'MEMBER', 'COLLABORATOR'}


@dataclass
class Opportunity:
    repo: str
    kind: str  # issue | todo | gap
    title: str
    score: float
    rationale: str
    issue_number: Optional[int] = None
    issue_url: Optional[str] = None
    issue_author_association: Optional[str] = None
    file_path: Optional[str] = None
    line: Optional[int] = None
    labels: List[str] = field(default_factory=list)
    already_has_open_pr: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _split_repo(full_name: str) -> tuple[str, str]:
    owner, _, name = full_name.partition('/')
    return owner, name


def _issue_ev(route: RepoRoute, issue: dict, claimed: bool) -> tuple[float, str]:
    """Heuristic expected-value score for solving an open issue via PR."""
    if claimed:
        return 0.0, 'already has an open PR referencing it'

    assoc = (issue.get('author_association') or '').upper()
    issue_mult = (
        route.scoring.maintainer_issue_multiplier
        if assoc in MAINTAINER_ASSOCS
        else route.scoring.standard_issue_multiplier
    )
    # Assume we can earn half of max known label if unlabeled_is_dead, else default.
    label_guess = (
        max(route.default_label_multiplier, route.max_known_label_multiplier * 0.5)
        if route.unlabeled_is_dead
        else max(1.0, route.default_label_multiplier)
    )
    base = route.fixed_base_score if route.fixed_base_score is not None else 10.0
    comments = int(issue.get('comments') or 0)
    # Prefer concrete, lightly-discussed maintainer issues on high PR-pool repos.
    engagement = 1.0 + min(comments, 5) * 0.05
    score = route.pr_pool_fraction * base * issue_mult * label_guess * engagement * 100.0
    rationale = (
        f'pr_pool={route.pr_pool_fraction:.4f} issue_mult={issue_mult} '
        f'label_guess={label_guess} assoc={assoc or "NONE"}'
    )
    return score, rationale


def _todo_ev(route: RepoRoute) -> float:
    base = route.fixed_base_score if route.fixed_base_score is not None else 5.0
    label_guess = 0.25 if route.unlabeled_is_dead else max(0.5, route.default_label_multiplier)
    return route.pr_pool_fraction * base * label_guess * 40.0


def _prs_claiming_issues(pulls: List[dict]) -> Set[int]:
    claimed: Set[int] = set()
    pat = re.compile(r'(?i)\b(?:fix(?:es)?|close[sd]?|resolve[sd]?)\s+#(\d+)')
    for pr in pulls:
        text = f"{pr.get('title') or ''}\n{pr.get('body') or ''}"
        for m in pat.finditer(text):
            claimed.add(int(m.group(1)))
    return claimed


def scan_todos(clone_dir: Path, cfg: PipelineConfig, limit: int = 40) -> List[tuple[str, int, str]]:
    """Return (relpath, line, text) TODO/FIXME hits."""
    hits: List[tuple[str, int, str]] = []
    if not clone_dir.is_dir():
        return hits

    skip = set(cfg.skip_path_parts)
    for path in clone_dir.rglob('*'):
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        if path.stat().st_size > 500_000:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            m = TODO_RE.search(line)
            if not m:
                continue
            rel = str(path.relative_to(clone_dir))
            hits.append((rel, i, m.group(0).strip()[:200]))
            if len(hits) >= limit:
                return hits
    return hits


def find_opportunities(
    routes: Iterable[RepoRoute],
    cfg: PipelineConfig,
    gh: Optional[GitHubClient] = None,
    clone_map: Optional[Dict[str, Path]] = None,
) -> List[Opportunity]:
    """Rank PR opportunities across emission-active repos."""
    gh = gh or GitHubClient(cfg.github_pat)
    clone_map = clone_map or {}
    opps: List[Opportunity] = []
    login = None
    try:
        if cfg.github_pat:
            login = (gh.whoami() or {}).get('login')
    except GitHubError:
        login = None

    for route in routes:
        if route.pr_pool_fraction <= 0:
            continue
        owner, name = _split_repo(route.full_name)
        try:
            issues = gh.list_open_issues(owner, name)
            pulls = gh.list_open_pulls(owner, name)
        except GitHubError as e:
            opps.append(
                Opportunity(
                    repo=route.full_name,
                    kind='gap',
                    title=f'github scan failed: {e}',
                    score=0.0,
                    rationale=str(e),
                )
            )
            continue

        claimed = _prs_claiming_issues(pulls)
        my_open = 0
        if login:
            my_open = sum(1 for p in pulls if (p.get('user') or {}).get('login') == login)

        for issue in issues:
            number = int(issue['number'])
            is_claimed = number in claimed
            score, rationale = _issue_ev(route, issue, is_claimed)
            if my_open >= route.open_pr_threshold(0.0):
                rationale += ' | WARNING: at/over open-PR threshold'
            labels = [l['name'] for l in issue.get('labels') or [] if isinstance(l, dict) and 'name' in l]
            opps.append(
                Opportunity(
                    repo=route.full_name,
                    kind='issue',
                    title=issue.get('title') or f'Issue #{number}',
                    score=score,
                    rationale=rationale,
                    issue_number=number,
                    issue_url=issue.get('html_url'),
                    issue_author_association=issue.get('author_association'),
                    labels=labels,
                    already_has_open_pr=is_claimed,
                )
            )

        clone_dir = clone_map.get(route.full_name.lower())
        if clone_dir:
            for rel, line, text in scan_todos(clone_dir, cfg):
                opps.append(
                    Opportunity(
                        repo=route.full_name,
                        kind='todo',
                        title=text,
                        score=_todo_ev(route),
                        rationale='local TODO/FIXME scan; prefer filing or linking an issue before PR',
                        file_path=rel,
                        line=line,
                    )
                )

    opps.sort(key=lambda o: (o.score, o.repo, o.issue_number or 0), reverse=True)
    return opps[: cfg.max_opportunities]
