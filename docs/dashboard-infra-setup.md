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
   - **service_role** secret API key (starts with `eyJ...`)

> The `service_role` key must stay secret. Maestro uses it to publish. Lovable uses only the `anon` key to read.

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

**Policy 2: Service-role write**
- Name: `Service role can upsert dashboards`
- Target roles: `service_role`
- Operation: `ALL`
- Using expression:
  ```sql
  true
  ```

> Because the `SELECT` policy is open, the only protection is the random `project_token`. This is intentional — it lets you share dashboard URLs with clients without login.

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
export MAESTRO_SUPABASE_SERVICE_KEY="your-service-role-key"
```

For persistence, add these to your shell profile (`~/.zshrc` or `~/.bashrc`).

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

- [ ] `MAESTRO_SUPABASE_SERVICE_KEY` is never committed to Git.
- [ ] Lovable only uses the `anon` key, not the service key.
- [ ] `project_token` is random and at least 32 characters.
- [ ] The Supabase `SELECT` policy is intentionally public; the token is the access control.
- [ ] If a dashboard is compromised, rotate the token in `.open-maestro/config.yaml` and re-publish.

---

## Next step

Once the Supabase project is created, implement the `SupabaseDashboardPublisher` in Maestro and wire it to `--publish-dashboard supabase`.
