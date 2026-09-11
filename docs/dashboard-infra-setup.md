# Dashboard Infrastructure Setup

Step-by-step directions for publishing Maestro dashboards to **merven.ai**.

- **Backend**: Lovable Cloud (Lovable's white-labeled Supabase). It speaks the
  standard Supabase REST API, which is what Maestro publishes to.
- **Frontend**: the merven.ai Lovable app, a standalone page at
  `/dashboard/<project_token>`.

Architecture: Maestro CLI upserts a dashboard snapshot into the
`maestro_dashboards` table; the Lovable app reads the row by `project_token`
and renders it. Access control is the random per-project token — no login.

---

## Phase 1: Create the backend (Lovable Cloud)

### Step 1: Create the table

Open the merven.ai project in Lovable and paste this prompt:

```
Create a `maestro_dashboards` table in this project's backend with columns:
id (uuid, primary key, default gen_random_uuid()), project_token (text,
unique), dashboard_json (jsonb), metadata (jsonb, default empty),
published_at (timestamptz, default now()), updated_at (timestamptz,
auto-refresh on change). Enable row-level security with public read by
anyone and upsert allowed with the publishable key.
```

Lovable confirms when the table is live and reports the publishing details,
e.g.:

```
URL: https://blmhsgltngpurywjkofo.supabase.co
Public key: sb_publishable_...
Endpoint: POST /rest/v1/maestro_dashboards?on_conflict=project_token
```

### Step 2: Save the credentials

Keep these two values — they are the only ones needed anywhere (Maestro CLI
and Lovable both use them):

- **URL**: `https://<project>.supabase.co`
- **Publishable key**: `sb_publishable_...`

The publishable key is public by design; it also ships in the Lovable
frontend. Nothing secret is involved in this setup.

---

## Phase 2: Create the dashboard page

### Step 3: Prompt Lovable for the page

In the merven.ai Lovable project:

```
Create a standalone page at route /dashboard/:token. It reads the token URL
parameter, fetches the row from the maestro_dashboards table where
project_token equals the token, and renders dashboard_json: project name,
overall completion percentage, and per-epic status with milestone progress
and blockers. Show a "Dashboard not found" state for unknown tokens and a
loading state while fetching.
```

Lovable Cloud injects the Supabase client and its environment variables
automatically — you do **not** need to write `createClient` code or edit
`.env` by hand. If the generated page references env vars, verify `.env`
contains `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` (Lovable
Cloud sets both); if anything is missing, ask Lovable to fix the connection.

### Step 4: Deploy

1. In Lovable, click **Deploy**.
2. If using a custom domain (merven.ai), ensure it is connected and SSL is active.

---

## Phase 3: Publish from Maestro

### Step 5: Set Maestro environment variables

On the machine where you run Maestro:

```bash
export MAESTRO_SUPABASE_URL="https://<project>.supabase.co"
export MAESTRO_SUPABASE_ANON_KEY="sb_publishable_..."
```

For persistence, add these to `~/.zshrc` or `~/.bashrc`.

### Step 6: Publish the dashboard

From inside your project folder:

```bash
maestro --publish-dashboard supabase
```

On first publish, Maestro will:
1. Generate a random `project_token` (>=32 chars).
2. Save it to `.open-maestro/config.yaml`.
3. Upsert the dashboard snapshot into `maestro_dashboards`.
4. Print the public URL:
   ```
   https://merven.ai/dashboard/<project_token>
   ```

Later publishes reuse the persisted token and update the same row.

### Step 7: Test the URL

Open the printed URL in a browser. You should see the rendered dashboard.

---

## Security checklist

- [ ] Only the publishable/anon key is used anywhere; no `service_role` key exists in this flow.
- [ ] `project_token` is random and at least 32 characters.
- [ ] The `SELECT` and write policies are intentionally public; the token is the access control for both reading and writing. Anyone who guesses a token can overwrite that dashboard — rotate the token if compromised.
- [ ] If a dashboard is compromised, rotate the token in `.open-maestro/config.yaml` and re-publish.

---

## Alternative: standalone Supabase project (no Lovable Cloud)

If the backend is a regular Supabase project instead of Lovable Cloud, create
the same table in the Supabase console (**Table Editor** → new table
`maestro_dashboards`, RLS enabled) with columns:

| Name | Type | Default | Other |
|---|---|---|---|
| `id` | `uuid` | `gen_random_uuid()` | Primary key |
| `project_token` | `text` | — | Unique |
| `dashboard_json` | `jsonb` | — | — |
| `metadata` | `jsonb` | `{}` | — |
| `published_at` | `timestamptz` | `now()` | — |
| `updated_at` | `timestamptz` | `now()` | — |

Add the same two RLS policies (**Authentication** → **Policies**), both with
using expression `true`:

1. `Public can read dashboards by token` — `SELECT` for `anon`, `authenticated`.
2. `Anon can upsert dashboards` — `ALL` for `anon`, `authenticated`.

Optionally add an `updated_at` auto-refresh trigger in the SQL Editor:

```sql
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger trigger_maestro_dashboards_updated_at
before update on public.maestro_dashboards
for each row
execute function public.set_updated_at();
```

The credentials are then the **Project URL** and the **anon public** key
(`eyJ...`) from **Project Settings** → **API** — use them in place of the
Lovable Cloud values in Steps 5-6. Everything else is identical.

---

## Status

Implemented in Maestro **v1.14.0**: `SupabaseDashboardPublisher`
(`src/open_maestro/milestones/supabase_publisher.py`), wired to
`maestro --publish-dashboard supabase`. See the CHANGELOG for details.
