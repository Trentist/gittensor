# Miner pipeline (PR-first)

Emission-aware operator tooling for Gittensor SN74. **Live registry currently pays on merged PRs**, not issue discovery (`issue_discovery_share: 0` on all master repos).

## Setup

```bash
cd /path/to/gittensor
uv sync
export GITTENSOR_MINER_PAT=ghp_...   # needs repo read; Issues/PRs write to open PRs
cp miner_pipeline/config.example.yaml miner_pipeline.yaml
```

Broadcast PAT once (subnet registration separate):

```bash
gitt miner post --wallet <name> --hotkey <hotkey>
```

## Daily loop

```bash
# 1) See where emissions go
python -m miner_pipeline sync
python -m miner_pipeline --json sync

# 2) Rank PR opportunities (open issues on paying repos)
python -m miner_pipeline opportunities
# optional TODO scan after shallow clone:
python -m miner_pipeline opportunities --clone
python -m miner_pipeline --json opportunities

# 3) Cut a branch/worktree
python -m miner_pipeline work --repo gittensor-ai-lab/sparkinfer --issue 12

# 4) Implement in the printed worktree, then plan/open PR (dry-run default)
python -m miner_pipeline pr \
  --repo gittensor-ai-lab/sparkinfer \
  --title "Fix …" \
  --summary "…" \
  --branch miner/issue-12 \
  --issue 12

# Actually open (after push):
python -m miner_pipeline pr ... --push --confirm --worktree ~/.gittensor/miner_pipeline/worktrees/...

# 5) Watch open-PR budget + unlabeled risk
python -m miner_pipeline monitor

# 6) Local validator score preview
python -m miner_pipeline score
# or: gitt miner score --pat "$GITTENSOR_MINER_PAT" --json
```

`--json` is a group flag: put it before the subcommand (`python -m miner_pipeline --json sync`).

## Rules that zero your score

- **Unlabeled PRs** on sparkinfer / bittrellis / hermes → `default_label_multiplier: 0` (chase `eval:*` / crown labels)
- **Too many open PRs** in a repo → spam multiplier 0
- **Self-merge without approval** → rejected
- **Maintainer association** on the repo → PRs ignored
- **Issue discovery filing** while share is 0 → no discovery emissions (drafts still saved)

## Discovery (when share flips)

```bash
python -m miner_pipeline discover --repo owner/name --title "…" --body-file draft.md
# refuses to file while issue_discovery_share == 0; always writes a draft under work_dir/drafts
python -m miner_pipeline discover ... --confirm   # files only if share > 0 and preflight passes
```

Prefer others solving your issues for discovery score. Linking **someone else's** issue from your PR still boosts the **PR** issue multiplier today.

## Commands

| Command | Purpose |
|---------|---------|
| `sync` | Registry + PR/discovery pool fractions |
| `opportunities` | Rank issues/TODOs by emission EV |
| `work` | Clone + worktree/branch |
| `pr` | Preflight + dry-run/open PR with `Fixes #N` |
| `monitor` | Open-PR budget / unlabeled warnings |
| `discover` | Draft/file issues (gated on discovery share) |
| `drafts` | List saved drafts |
| `score` | Wrap `gitt miner score --json` |
