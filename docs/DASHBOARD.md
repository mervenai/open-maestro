# Dashboard Setup & Updating

> **Status:** publishing to **merven.ai** is live since v1.14.0 via Supabase
> (Lovable Cloud) — see "Publish to merven.ai" below and
> [dashboard-infra-setup.md](dashboard-infra-setup.md) for the one-time setup.
> This document also covers export, local serve, the self-hosted receiver,
> and the legacy Merven integration.

Referenced from [DEPLOY.md](DEPLOY.md). For workstation install and runtime
setup, see the deploy guide.

Maestro tracks project milestones and exposes a client-facing dashboard. In v1.3.0
the taxonomy changed: a project contains **epics** (workstreams / features such as
"Import Flow" or "Audit Log"), and each epic contains the 8 standard lifecycle
**milestones** (Intake & Discovery, Execution Planning, Design Blueprint, etc.).

### Export a dashboard (local only)

`--export-dashboard` renders the current milestone plan to stdout. It does **not**
upload anything to a remote server. To push a dashboard to a hosted receiver, use
`--publish-dashboard` instead.

From inside a project directory:

```bash
# JSON
maestro --export-dashboard json > dashboard.json

# Markdown
maestro --export-dashboard markdown > dashboard.md

# HTML (styled like merven.ai)
maestro --export-dashboard html > dashboard.html
```

### Publish to merven.ai (recommended)

Since v1.14.0, Maestro publishes dashboard snapshots to Supabase (Lovable
Cloud); the merven.ai Lovable app renders them at
`https://merven.ai/dashboard/<project_token>`. One-time setup (table, page,
env vars) is in [dashboard-infra-setup.md](dashboard-infra-setup.md).

From inside a project directory:

```bash
export MAESTRO_SUPABASE_URL="https://<project>.supabase.co"
export MAESTRO_SUPABASE_ANON_KEY="sb_publishable_..."
maestro --publish-dashboard supabase
```

First publish generates a random project token and saves it to
`.open-maestro/config.yaml`; later publishes reuse it and update the same
row. The command prints the public URL.

### Link a Maestro project to Merven

To pull the canonical epic/workstream structure from Merven, you need a Merven
project and its token. This is done on the Merven core server, not the engineer
workstation.

#### 1. Create the project on the Merven server

SSH into the Merven core server and run:

```bash
cd /opt/merven
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec core merven project create "Project Name" \
  --client-name "Client Name" \
  --epic "Epic One" \
  --epic "Epic Two"
```

The command prints a project ID and token. If you only have the project ID, the
token is stored in the `project` table under the tenant schema in Postgres:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec postgres psql -U merven -d merven -t -A \
  -c "SET search_path TO tenant_acme_corp; SELECT project_token FROM project WHERE project_id='PROJECT_ID';"
```

(Replace `tenant_acme_corp` with the correct tenant schema if different.)

#### 2. Delete a project on Merven

The `merven project` CLI does not expose a `delete` action, so removing a project
requires deleting its row directly from the Merven Postgres database.

> **Warning:** This is irreversible. Back up the database or export the dashboard
> first if you need to preserve anything.

Run these commands on the Merven core server:

```bash
cd /opt/merven

# 1. Confirm the project exists and note the correct tenant schema.
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec postgres psql -U merven -d merven -t -A \
  -c "SET search_path TO tenant_acme_corp; SELECT project_id, name FROM project WHERE project_id='PROJECT_ID';"

# 2. Delete the project row. If foreign-key constraints block this, the error
#    will list the dependent table(s); delete those rows first.
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec postgres psql -U merven -d merven -t -A \
  -c "SET search_path TO tenant_acme_corp; DELETE FROM project WHERE project_id='PROJECT_ID';"
```

To discover all tables in a tenant schema before deleting:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec postgres psql -U merven -d merven -t -A \
  -c "SELECT tablename FROM pg_tables WHERE schemaname='tenant_acme_corp';"
```

After deleting on the server, remove the local milestone file on the workstation
to unlink the project:

```bash
cd ~/projects/YourProject
rm .open-maestro/milestones.yaml
```

#### 3. Sync milestones to the local workstation

On the engineer machine, from the project directory:

```bash
cd ~/projects/YourProject
export MERVEN_API_URL="https://api.staging.merven.ai"
export MERVEN_API_KEY="demo"
export MAESTRO_PROJECT_ID="project-id-from-above"
maestro --sync-milestones
```

`MERVEN_API_URL` is the Merven core API base path (without `/maestro`).
`MAESTRO_PROJECT_ID` is the project **ID** from Merven (not the dashboard project token).
`MERVEN_API_KEY` is the Merven core API key from the server's `deploy/.env`
(`MERVEN_API_KEY` for static mode, or `MERVEN_TENANT_DEFAULT_API_KEY` for
`per_tenant` mode). It is **not** the dashboard publish key. The staging
deployment currently uses the default key `demo`; production deployments should
use a generated key.
The local plan is written to `.open-maestro/milestones.yaml`.

If you only have the dashboard project token, Maestro will try to resolve it to a
project ID by reading the published dashboard snapshot, but using the project ID is
faster and more reliable.

**Note:** `--sync-milestones` requires the workstation to reach `MERVEN_API_URL`.
If the workstation is behind a heavy firewall or VPN that blocks outbound HTTPS,
sync will time out. In that case, create and manage milestones locally with
`maestro --discover-milestones` and the `/track`, `/complete`, `/blocker`
interactive commands.

### Serve the dashboard locally

```bash
maestro --serve-dashboard --dashboard-port 8080
```

Then open http://localhost:8080 in a browser. Endpoints:

- `/` — HTML dashboard
- `/api/dashboard` — JSON dashboard
- `/dashboard.md` — Markdown dashboard

### Standalone dashboard receiver (no Merven required)

Maestro ships a self-hosted dashboard receiver that stores published snapshots
as JSON files and serves HTML/JSON/Markdown views. It does **not** need the
Merven database or Merven core API.

#### Run the receiver on a server

Install the Maestro wheel on the host (or any machine with Python 3.11+) and run:

```bash
export MAESTRO_DASHBOARD_API_KEY="your-secret-key"
maestro --serve-remote-dashboard --dashboard-host 0.0.0.0 --dashboard-port 8080 --dashboard-data-dir /var/lib/maestro-dashboards
```

- `--dashboard-host 0.0.0.0` binds to all interfaces so a reverse proxy can reach it.
- `--dashboard-data-dir` is where snapshots are stored (default: `./.open-maestro/dashboards`).
- `MAESTRO_DASHBOARD_API_KEY` is required for publishing. GET views are public.

For production, put the receiver behind a reverse proxy with HTTPS. A minimal
Caddyfile:

```
dashboards.example.com {
    reverse_proxy localhost:8080
}
```

#### Publish from a workstation

From inside a Maestro-linked project directory:

```bash
export MAESTRO_DASHBOARD_URL="https://dashboards.example.com/maestro/dashboard"
export MAESTRO_DASHBOARD_API_KEY="your-secret-key"
export MAESTRO_DASHBOARD_PROJECT_TOKEN="project-token"

maestro --publish-dashboard "$MAESTRO_DASHBOARD_URL"
```

The project token can be any string you choose; it becomes the URL slug for the
dashboard. The receiver creates the snapshot on first publish.

#### View a published dashboard

Given a project token of `project-token`:

- HTML: `https://dashboards.example.com/maestro/dashboard/project-token/html`
- JSON: `https://dashboards.example.com/maestro/dashboard/project-token`
- Markdown: `https://dashboards.example.com/maestro/dashboard/project-token/md`

#### Delete a published snapshot

Snapshots are plain JSON files in `--dashboard-data-dir`. Delete the file named
after the project token:

```bash
rm /var/lib/maestro-dashboards/project-token.json
```

### Publish to a remote receiver (legacy Merven integration)

The original receiver lives in the Merven core API on `api.staging.merven.ai`
(the `merven.ai` root site is hosted on Lovable and cannot run a Python
backend). On the Merven core server, set `MERVEN_MAESTRO_DASHBOARD_API_KEY` in
`deploy/.env`. On the Maestro CLI, use `MAESTRO_DASHBOARD_API_KEY`. Both values
must be identical.

```bash
export MAESTRO_DASHBOARD_URL="https://api.staging.merven.ai/maestro/dashboard"
export MAESTRO_DASHBOARD_API_KEY="ff10f1dc1d1d41099aa9ae5d21db8423521841ec80d05f9ef3b7e14b309bc73b"
export MAESTRO_DASHBOARD_PROJECT_TOKEN="project-token"

maestro --publish-dashboard "$MAESTRO_DASHBOARD_URL"
```

Or pass everything inline:

```bash
maestro --publish-dashboard https://api.staging.merven.ai/maestro/dashboard --dashboard-api-key "your-api-key" --dashboard-project-token "project-token"
```

On the Merven server, make sure `deploy/.env` contains:

```bash
MERVEN_MAESTRO_DASHBOARD_API_KEY="your-api-key"
```

And deploy with `--env-file deploy/.env` from `/opt/merven` so Compose reads the file:

```bash
cd /opt/merven
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml up -d
```

The receiver accepts a POST with this payload:

```json
{
  "dashboard": {
    "project_id": "...",
    "project_name": "...",
    "overall_completion": 42,
    "current_milestone": ["import-flow/implementation"],
    "active_blockers": [],
    "epics": [
      {
        "id": "import-flow",
        "name": "Import Flow",
        "completion": 65,
        "milestones": [...]
      }
    ],
    "recent_deliverables": [...]
  },
  "metadata": {
    "source": "maestro-cli"
  }
}
```
