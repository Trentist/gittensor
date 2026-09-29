"""CLI for the emission-aware miner pipeline."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from miner_pipeline.config import load_config
from miner_pipeline.discovery import file_issue, list_drafts
from miner_pipeline.github_api import GitHubClient, GitHubError
from miner_pipeline.monitor import health_to_payload, monitor_routes
from miner_pipeline.opportunities import find_opportunities
from miner_pipeline.pr import detect_default_branch, open_pull_request, plan_pull_request, push_branch
from miner_pipeline.registry import build_routes, routes_to_dicts
from miner_pipeline.score_loop import run_miner_score
from miner_pipeline.workspace import WorkspaceError, create_worktree, ensure_clone

console = Console()


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        console.print(data)


@click.group()
@click.option('--config', 'config_path', type=click.Path(path_type=Path), default=None)
@click.option('--json', 'as_json', is_flag=True, help='Emit JSON')
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[Path], as_json: bool) -> None:
    """Gittensor miner pipeline — PR-first, emission-aware."""
    ctx.ensure_object(dict)
    cfg = load_config(config_path)
    cfg.ensure_dirs()
    ctx.obj['cfg'] = cfg
    ctx.obj['json'] = as_json


@cli.command('sync')
@click.pass_context
def sync_cmd(ctx: click.Context) -> None:
    """Show master repos and emission routes (PR vs discovery)."""
    cfg = ctx.obj['cfg']
    routes = build_routes(allowlist=cfg.repo_allowlist or None)
    payload = {
        'routes': routes_to_dicts(routes),
        'note': 'While issue_discovery_share=0, compete on merged PRs (trusted labels).',
    }
    if ctx.obj['json']:
        _emit(payload, True)
        return

    table = Table(title='Emission routes')
    table.add_column('Repo')
    table.add_column('PR pool', justify='right')
    table.add_column('Discovery', justify='right')
    table.add_column('Route')
    table.add_column('Unlabeled dead?')
    for row in payload['routes']:
        table.add_row(
            row['repo'],
            f"{row['pr_pool_fraction']:.4f}",
            f"{row['discovery_pool_fraction']:.4f}",
            row['recommended_route'],
            'yes' if row['unlabeled_is_dead'] else 'no',
        )
    console.print(table)
    console.print(payload['note'])


@cli.command('opportunities')
@click.option('--clone/--no-clone', default=False, help='Shallow-clone repos and scan TODOs')
@click.pass_context
def opportunities_cmd(ctx: click.Context, clone: bool) -> None:
    """Rank PR opportunities on emission-active repos."""
    cfg = ctx.obj['cfg']
    routes = build_routes(allowlist=cfg.repo_allowlist or None)
    clone_map = {}
    if clone:
        for route in routes:
            if route.pr_pool_fraction <= 0:
                continue
            try:
                path = ensure_clone(route.full_name, cfg, token=cfg.github_pat)
                clone_map[route.full_name.lower()] = path
            except WorkspaceError as e:
                console.print(f'[yellow]clone failed {route.full_name}: {e}[/yellow]')

    gh = GitHubClient(cfg.github_pat)
    opps = find_opportunities(routes, cfg, gh=gh, clone_map=clone_map)
    payload = {'opportunities': [o.to_dict() for o in opps]}
    if ctx.obj['json']:
        _emit(payload, True)
        return

    table = Table(title='PR opportunities')
    table.add_column('Score', justify='right')
    table.add_column('Repo')
    table.add_column('Kind')
    table.add_column('Title')
    table.add_column('Issue')
    for o in opps:
        table.add_row(
            f'{o.score:.2f}',
            o.repo,
            o.kind,
            (o.title or '')[:60],
            str(o.issue_number or '-'),
        )
    console.print(table)


@cli.command('work')
@click.option('--repo', required=True, help='owner/name')
@click.option('--issue', type=int, default=None)
@click.option('--title', default='miner-work')
@click.pass_context
def work_cmd(ctx: click.Context, repo: str, issue: Optional[int], title: str) -> None:
    """Create a worktree/branch for implementing a fix."""
    cfg = ctx.obj['cfg']
    try:
        ws = create_worktree(repo.lower(), cfg, issue_number=issue, title=title, token=cfg.github_pat)
    except WorkspaceError as e:
        _emit({'ok': False, 'error': str(e)}, ctx.obj['json'])
        sys.exit(1)
    _emit({'ok': True, 'workspace': ws.to_dict()}, ctx.obj['json'])


@cli.command('pr')
@click.option('--repo', required=True)
@click.option('--title', required=True)
@click.option('--summary', required=True)
@click.option('--branch', required=True, help='Head branch name already pushed or local')
@click.option('--issue', type=int, default=None, help='Issue number to Fixes #N')
@click.option('--base', default=None)
@click.option('--confirm', is_flag=True, help='Actually open the PR (overrides dry-run)')
@click.option('--push/--no-push', default=False, help='Push branch before opening')
@click.option('--worktree', type=click.Path(path_type=Path), default=None)
@click.pass_context
def pr_cmd(
    ctx: click.Context,
    repo: str,
    title: str,
    summary: str,
    branch: str,
    issue: Optional[int],
    base: Optional[str],
    confirm: bool,
    push: bool,
    worktree: Optional[Path],
) -> None:
    """Plan / open a PR under emission + spam preflight."""
    cfg = ctx.obj['cfg']
    routes = {r.full_name.lower(): r for r in build_routes()}
    route = routes.get(repo.lower())
    if not route:
        _emit({'ok': False, 'error': f'{repo} not in master registry'}, ctx.obj['json'])
        sys.exit(1)

    gh = GitHubClient(cfg.github_pat)
    owner, _, name = repo.partition('/')
    open_prs_by_me = 0
    try:
        if cfg.github_pat:
            login = (gh.whoami() or {}).get('login')
            pulls = gh.list_open_pulls(owner, name)
            open_prs_by_me = sum(1 for p in pulls if (p.get('user') or {}).get('login') == login)
    except GitHubError:
        pass

    clone_dir = cfg.clones_dir / owner / name
    if base is None:
        base = detect_default_branch(clone_dir) if clone_dir.is_dir() else 'main'

    if push:
        wt = worktree or clone_dir
        try:
            push_msg = push_branch(wt, branch, dry_run=not (confirm or cfg.auto_pr))
            console.print(push_msg)
        except RuntimeError as e:
            _emit({'ok': False, 'error': str(e)}, ctx.obj['json'])
            sys.exit(1)

    plan = plan_pull_request(
        route,
        cfg,
        title=title,
        summary=summary,
        head=branch,
        base=base,
        issue_number=issue,
        open_prs_by_me=open_prs_by_me,
        confirm=confirm,
    )
    result = open_pull_request(plan, cfg, confirm=confirm, gh=gh)
    _emit(result, ctx.obj['json'])
    if not result.get('ok'):
        sys.exit(1)


@cli.command('monitor')
@click.pass_context
def monitor_cmd(ctx: click.Context) -> None:
    """Check open PR budgets, unlabeled PRs, discovery status."""
    cfg = ctx.obj['cfg']
    routes = build_routes(allowlist=cfg.repo_allowlist or None)
    health = monitor_routes(routes, cfg)
    payload = health_to_payload(health)
    if ctx.obj['json']:
        _emit(payload, True)
        return
    for h in health:
        console.print(f'[bold]{h.repo}[/bold] open_prs={h.my_open_prs}/{h.open_pr_threshold} open_issues={h.my_open_issues}')
        for w in h.warnings:
            console.print(f'  [yellow]{w}[/yellow]')
        for u in h.unlabeled_open_prs:
            console.print(f'  unlabeled: {u}')


@cli.command('discover')
@click.option('--repo', required=True)
@click.option('--title', required=True)
@click.option('--body-file', type=click.Path(path_type=Path), default=None)
@click.option('--body', default=None)
@click.option('--confirm', is_flag=True)
@click.pass_context
def discover_cmd(
    ctx: click.Context,
    repo: str,
    title: str,
    body_file: Optional[Path],
    body: Optional[str],
    confirm: bool,
) -> None:
    """Draft (and only file if discovery share > 0) an issue."""
    cfg = ctx.obj['cfg']
    routes = {r.full_name.lower(): r for r in build_routes()}
    route = routes.get(repo.lower())
    if not route:
        _emit({'ok': False, 'error': f'{repo} not in master registry'}, ctx.obj['json'])
        sys.exit(1)

    if body_file:
        text = body_file.read_text(encoding='utf-8')
    else:
        text = body or '## Problem\n\n_TBD_\n'
    result = file_issue(
        route,
        cfg,
        title=title,
        body=text,
        open_issues_by_me=0,
        confirm=confirm,
    )
    _emit(result, ctx.obj['json'])
    if not result.get('ok') and not result.get('draft_path'):
        sys.exit(1)


@cli.command('drafts')
@click.pass_context
def drafts_cmd(ctx: click.Context) -> None:
    """List saved issue drafts."""
    cfg = ctx.obj['cfg']
    _emit({'drafts': list_drafts(cfg)}, True)


@cli.command('score')
@click.pass_context
def score_cmd(ctx: click.Context) -> None:
    """Run `gitt miner score --json` health check."""
    cfg = ctx.obj['cfg']
    result = run_miner_score(pat=cfg.github_pat, json_mode=True)
    _emit(result, True)


def main() -> None:
    # Allow AUTO_PR / AUTO_FILE env overrides
    cfg_bootstrap = load_config()
    if os.environ.get('AUTO_PR', '').lower() in {'1', 'true', 'yes'}:
        cfg_bootstrap.auto_pr = True
    if os.environ.get('AUTO_FILE', '').lower() in {'1', 'true', 'yes'}:
        cfg_bootstrap.auto_file_issues = True
    cli(obj={})


if __name__ == '__main__':
    main()
