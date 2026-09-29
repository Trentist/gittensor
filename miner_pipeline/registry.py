"""Load master repo registry and compute emission-aware routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from gittensor.constants import OSS_EMISSION_SHARE
from gittensor.validator.oss_contributions.scoring import calculate_open_pr_threshold
from gittensor.validator.issue_discovery.scoring import calculate_open_issue_spam_multiplier
from gittensor.validator.utils.load_weights import (
    RepositoryConfig,
    ResolvedEligibility,
    ResolvedScoring,
    load_master_repo_weights,
    resolve_eligibility,
    resolve_scoring,
)


@dataclass(frozen=True)
class RepoRoute:
    """Emission-aware view of one master repository."""

    full_name: str
    emission_share: float
    issue_discovery_share: float
    pr_pool_fraction: float
    discovery_pool_fraction: float
    default_label_multiplier: float
    label_multipliers: Dict[str, float]
    fixed_base_score: Optional[float]
    trusted_label_pipeline: bool
    eligibility: ResolvedEligibility
    scoring: ResolvedScoring
    maintainer_cut: float

    @property
    def discovery_enabled(self) -> bool:
        return self.issue_discovery_share > 0.0

    @property
    def unlabeled_is_dead(self) -> bool:
        """True when unlabeled PRs effectively score zero via label multiplier."""
        return self.default_label_multiplier <= 0.0

    @property
    def max_known_label_multiplier(self) -> float:
        if not self.label_multipliers:
            return self.default_label_multiplier
        return max(self.default_label_multiplier, max(self.label_multipliers.values()))

    def open_pr_threshold(self, token_score: float = 0.0) -> int:
        return calculate_open_pr_threshold(self.eligibility, token_score)

    def open_issue_allowed(self, open_issues: int, solved_token_score: float = 0.0) -> bool:
        return (
            calculate_open_issue_spam_multiplier(self.eligibility, open_issues, solved_token_score) > 0.0
        )


def build_routes(
    repos: Optional[Dict[str, RepositoryConfig]] = None,
    allowlist: Optional[List[str]] = None,
) -> List[RepoRoute]:
    """Build sorted routes (highest PR-pool EV first)."""
    repos = repos or load_master_repo_weights()
    allow = {a.lower() for a in (allowlist or [])}
    routes: List[RepoRoute] = []

    for full_name, cfg in repos.items():
        if allow and full_name.lower() not in allow:
            continue
        elig = resolve_eligibility(cfg.eligibility)
        scoring = resolve_scoring(cfg.scoring)
        pr_frac = cfg.emission_share * (1.0 - cfg.issue_discovery_share) * OSS_EMISSION_SHARE
        disc_frac = cfg.emission_share * cfg.issue_discovery_share * OSS_EMISSION_SHARE
        routes.append(
            RepoRoute(
                full_name=full_name,
                emission_share=cfg.emission_share,
                issue_discovery_share=cfg.issue_discovery_share,
                pr_pool_fraction=pr_frac,
                discovery_pool_fraction=disc_frac,
                default_label_multiplier=cfg.default_label_multiplier,
                label_multipliers=dict(cfg.label_multipliers or {}),
                fixed_base_score=cfg.fixed_base_score,
                trusted_label_pipeline=cfg.trusted_label_pipeline,
                eligibility=elig,
                scoring=scoring,
                maintainer_cut=cfg.maintainer_cut,
            )
        )

    routes.sort(key=lambda r: (r.pr_pool_fraction, r.discovery_pool_fraction, r.full_name), reverse=True)
    return routes


def routes_to_dicts(routes: List[RepoRoute]) -> List[dict]:
    """JSON-serializable summary for CLI output."""
    out = []
    for r in routes:
        out.append(
            {
                'repo': r.full_name,
                'emission_share': r.emission_share,
                'issue_discovery_share': r.issue_discovery_share,
                'pr_pool_fraction': round(r.pr_pool_fraction, 6),
                'discovery_pool_fraction': round(r.discovery_pool_fraction, 6),
                'discovery_enabled': r.discovery_enabled,
                'unlabeled_is_dead': r.unlabeled_is_dead,
                'max_label_multiplier': r.max_known_label_multiplier,
                'fixed_base_score': r.fixed_base_score,
                'open_pr_threshold_base': r.open_pr_threshold(0.0),
                'recommended_route': 'pr' if r.pr_pool_fraction > 0 else (
                    'discovery' if r.discovery_enabled else 'skip'
                ),
            }
        )
    return out
