"""Thin GitHub REST helpers for the miner pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

GITHUB_API = 'https://api.github.com'


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: Optional[str] = None, timeout: float = 30.0):
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'gittensor-miner-pipeline',
        }
        if token:
            headers['Authorization'] = f'Bearer {token}'
        self.session.headers.update(headers)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = path if path.startswith('http') else f'{GITHUB_API}{path}'
        resp = self.session.get(url, params=params, timeout=self.timeout)
        if resp.status_code >= 400:
            raise GitHubError(f'GET {path} -> {resp.status_code}: {resp.text[:300]}')
        if resp.status_code == 204:
            return None
        return resp.json()

    def _post(self, path: str, payload: dict) -> Any:
        url = path if path.startswith('http') else f'{GITHUB_API}{path}'
        resp = self.session.post(url, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise GitHubError(f'POST {path} -> {resp.status_code}: {resp.text[:300]}')
        return resp.json()

    def whoami(self) -> Dict[str, Any]:
        return self._get('/user')

    def list_open_issues(self, owner: str, repo: str, per_page: int = 50, max_pages: int = 5) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = self._get(
                f'/repos/{owner}/{repo}/issues',
                params={'state': 'open', 'per_page': per_page, 'page': page},
            )
            if not batch:
                break
            # GitHub returns PRs in the issues endpoint; drop them.
            issues = [i for i in batch if 'pull_request' not in i]
            items.extend(issues)
            if len(batch) < per_page:
                break
        return items

    def list_open_pulls(self, owner: str, repo: str, per_page: int = 50, max_pages: int = 5) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = self._get(
                f'/repos/{owner}/{repo}/pulls',
                params={'state': 'open', 'per_page': per_page, 'page': page},
            )
            if not batch:
                break
            items.extend(batch)
            if len(batch) < per_page:
                break
        return items

    def create_issue(self, owner: str, repo: str, title: str, body: str, labels: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {'title': title, 'body': body}
        if labels:
            payload['labels'] = labels
        return self._post(f'/repos/{owner}/{repo}/issues', payload)

    def create_pull(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = False,
    ) -> Dict[str, Any]:
        return self._post(
            f'/repos/{owner}/{repo}/pulls',
            {'title': title, 'body': body, 'head': head, 'base': base, 'draft': draft},
        )

    def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        return self._get(f'/repos/{owner}/{repo}')
