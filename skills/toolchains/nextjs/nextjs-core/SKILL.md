---
id: nextjs-core
name: Nextjs Core
tags:
- nextjs
- app-router
---

# Next.js Core (App Router)

- Server Components by default; minimal `"use client"`.
- Mutations in Server Actions (validate/authz; revalidate tags/paths).
- Route handlers for APIs/webhooks; add loading/error boundaries.

Anti-patterns:
- ❌ Fetch initial data in `useEffect`.
- ❌ Cache or revalidate too broadly.
- ❌ Client-only authz.

References: see `references/` (server actions, fetching, caching, routing, auth, testing).