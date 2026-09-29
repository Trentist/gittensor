"""Optional wrapper around `gitt miner score` for health checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional


def run_miner_score(
    *,
    pat: Optional[str] = None,
    json_mode: bool = True,
    extra_args: Optional[list[str]] = None,
) -> Dict[str, Any]:
    """Invoke `gitt miner score` in-process via CLI subprocess."""
    pat = pat or os.environ.get('GITTENSOR_MINER_PAT') or os.environ.get('GITHUB_TOKEN')
    if not pat:
        return {'ok': False, 'error': 'PAT required (GITTENSOR_MINER_PAT)'}

    cmd = [sys.executable, '-m', 'gittensor.cli.main', 'miner', 'score', '--pat', pat]
    # `gitt` console script is equivalent when the env is installed.
    if json_mode:
        cmd.append('--json')
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {
            'ok': False,
            'error': (proc.stderr or proc.stdout)[-4000:],
            'returncode': proc.returncode,
        }

    if json_mode:
        try:
            return {'ok': True, 'result': json.loads(proc.stdout)}
        except json.JSONDecodeError:
            return {'ok': True, 'raw': proc.stdout}

    return {'ok': True, 'raw': proc.stdout}
