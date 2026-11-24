# Jira Sync Runbook

Maestro's own development stories live in `docs/maestro-user-stories.csv` (the local
source of truth) and are mirrored to the **MSTRO** project on
`https://mervenai.atlassian.net` by `scripts/sync_jira.py`.

## Setup (once)

Credentials live outside the repo, in `~/.config/maestro/jira.env`:

```bash
mkdir -p ~/.config/maestro
cat > ~/.config/maestro/jira.env <<'EOF'
JIRA_EMAIL=you@merven.ai
JIRA_API_TOKEN=<atlassian-api-token>
EOF
chmod 600 ~/.config/maestro/jira.env
```

The token is an Atlassian API token (id.atlassian.com -> Security -> API tokens).

## Release ritual (post-tag, after pushing main)

1. **Append a story per shipped feature** to `docs/maestro-user-stories.csv`
   (next `MAE-nnn`, Status `Done`, kebab-case labels, Given/When/Then acceptance).
2. Dry-run to preview: `./scripts/sync_jira.sh --dry-run`
3. Push to Jira: `./scripts/sync_jira.sh --create-missing`
4. Commit the CSV with the release commit or right after it.

## How the sync maps rows to issues

- Matching is by **exact summary text** (case-insensitive) — rename a story in the
  CSV and the sync treats it as new; avoid renaming shipped stories.
- Statuses map 1:1 (`Done`/`In Progress`/`Backlog`). **CSV is authoritative**: a
  stale CSV status overwrites Jira state, so never run step 3 with unreviewed
  status changes (the dry run prints every transition it would make).
- New stories are created as `Story` issues with the description + acceptance
  criteria and the Labels column as Jira labels.

## Notes

- Env vars `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_BASE_URL` / `JIRA_PROJECT`
  (default `MSTRO`) override the env file if already set in the shell.
- The wrapper picks the repo `.venv` interpreter when present (httpx required).
- If Jira shows work `Done` that the CSV doesn't (e.g. marked complete in the UI
  directly), fix the CSV first — see MAE-060's history for why: the sync will
  happily regress Jira to match a stale CSV.
