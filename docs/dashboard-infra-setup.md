# Dashboard Infrastructure Setup

Step-by-step directions for publishing Maestro dashboards to **merven.ai** using **Supabase** as the backend and **Lovable** as the frontend host.

Architecture: Maestro CLI publishes a dashboard snapshot to Supabase; Lovable serves a standalone page at `https://merven.ai/dashboard/<project_token>`.

---

## Phase 1: Supabase backend

### Step 1: Create a Supabase project

1. Go to [https://supabase.com](https://supabase.com) and sign in.
2. Click **New project**.
3. Choose your organization.
4. Fill in:
   - **Project name**: `merven-dashboard` (or any name)
   - **Database password**: strong password, save it in a password manager
   - **Region**: pick the region closest to your users
5. Click **Create new project** and wait ~2 minutes for provisioning.

### Step 2: Get your Supabase credentials

1. In the Supabase dashboard, go to **Project Settings** → **API**.
2. Copy and save these values:
   - **Project URL** (e.g. `https://abcdefghijklmnop.supabase.co`)
   - **anon public** API key (starts with `eyJ...`)

> Only the `anon` key is needed. Maestro publishes with it and Lovable reads with it. The `service_role` key is not used by this setup and should stay disabled/secret.

### Step 3: Create the dashboards table

1. In Supabase, go to **Table Editor**.
2. Click **New table**.
3. Name it `maestro_dashboards`.
4. Enable **Row Level Security (RLS)**.
5. Add these columns:

| Name | Type | Default | Other |
|---|---|---|---|
| `id` | `uuid` | `gen_random_uuid()` | Primary key |
| `project_token` | `text` | — | Unique |
| `dashboard_json` | `jsonb` | — | — |
| `metadata` | `jsonb` | `{}` | — |
| `published_at` | `timestamptz` | `now()` | — |
| `updated_at` | `timestamptz` | `now()` | — |

6. Click **Save**.

### Step 4: Add RLS policies

1. Go to **Authentication** → **Policies**.
2. Find `maestro_dashboards` and create these policies:

**Policy 1: Public read**
- Name: `Public can read dashboards by token`
- Target roles: `anon`, `authenticated`
- Operation: `SELECT`
- Using expression:
  ```sql
  true
  ```

**Policy 2: Public upsert (publishing)**
- Name: `Anon can upsert dashboards`
- Target roles: `anon`, `authenticated`
- Operation: `ALL`
- Using expression:
  ```sql
  true
  ```

> The write policy is intentionally open because publishing uses the public `anon` key — anyone who can reach the Supabase API can upsert a row if they know its `project_token`. The random token (>=32 chars) is the access control, for both reading and writing.

### Step 5: (Optional) Add updated_at trigger

In the **SQL Editor**, run:

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

---

## Phase 2: Lovable frontend

### Step 6: Create the dashboard page

1. Open your merven.ai project in Lovable.
2. In the chat/agent prompt, say:

   ```
   Create a new page at route /dashboard/:token that reads a token URL parameter, fetches a dashboard snapshot from Supabase, and renders it.
   ```

3. Lovable should create a page component. If it does not create a dynamic route, prompt:

   ```
   Use React Router v6 useParams to read the token from /dashboard/:token.
   ```

### Step 7: Add Supabase fetch logic

In the dashboard page component, Lovable should generate code similar to:

```tsx
import { useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { createClient } from "@supabase/supabase-js";

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY
);

export default function DashboardPage() {
  const { token } = useParams();
  const [dashboard, setDashboard] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;

    supabase
      .from("maestro_dashboards")
      .select("dashboard_json")
      .eq("project_token", token)
      .single()
      .then(({ data, error }) => {
        if (error || !data) {
          setError("Dashboard not found");
        } else {
          setDashboard(data.dashboard_json);
        }
      });
  }, [token]);

  if (error) return <div className="p-8">{error}</div>;
  if (!dashboard) return <div className="p-8">Loading...</div>;

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold">{dashboard.project_name}</h1>
      <p>Overall completion: {dashboard.summary?.overall_completion}%</p>
      {/* Render epics, milestones, blockers here */}
    </div>
  );
}
```

### Step 8: Configure Supabase environment variables in Lovable

1. In Lovable, open **Code** → `.env`.
2. Add:

   ```
   VITE_SUPABASE_URL=https://abcdefghijklmnop.supabase.co
   VITE_SUPABASE_PUBLISHABLE_KEY=your-anon-key
   ```

3. Save. Lovable will embed these at build time.

### Step 9: Add route to App.tsx

If Lovable did not add it automatically, ensure `src/App.tsx` has:

```tsx
<Route path="/dashboard/:token" element={<DashboardPage />} />
```

### Step 10: Deploy the Lovable app

1. In Lovable, click **Deploy**.
2. If using a custom domain (merven.ai), ensure it is connected and SSL is active.

---

## Phase 3: Maestro publisher setup

### Step 11: Set Maestro environment variables

On the machine where you run Maestro:

```bash
export MAESTRO_SUPABASE_URL="https://abcdefghijklmnop.supabase.co"
export MAESTRO_SUPABASE_ANON_KEY="your-anon-key"
```

The anon key is public by design (it also ships in the Lovable frontend), so it is safe to keep in your shell profile. For persistence, add these to `~/.zshrc` or `~/.bashrc`.

### Step 12: Publish the dashboard

From inside your project folder:

```bash
maestro --publish-dashboard supabase
```

On first publish, Maestro will:
1. Generate a random `project_token`.
2. Save it to `.open-maestro/config.yaml`.
3. Upsert the dashboard snapshot into Supabase.
4. Print the public URL:
   ```
   https://merven.ai/dashboard/<project_token>
   ```

### Step 13: Test the URL

Open the printed URL in a browser. You should see the rendered dashboard.

---

## Phase 4: Security checklist

- [ ] Only the `anon` key is used anywhere; the `service_role` key stays disabled/unused.
- [ ] `project_token` is random and at least 32 characters.
- [ ] The Supabase `SELECT` and write policies are intentionally public; the token is the access control for both reading and writing. Anyone who guesses a token can overwrite that dashboard — rotate the token if compromised.
- [ ] If a dashboard is compromised, rotate the token in `.open-maestro/config.yaml` and re-publish.

---

## Status

Implemented in Maestro **v1.14.0**: `SupabaseDashboardPublisher`
(`src/open_maestro/milestones/supabase_publisher.py`), wired to
`maestro --publish-dashboard supabase`. See the CHANGELOG for details.
