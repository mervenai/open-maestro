---
id: nextjs-v16
name: Nextjs V16
tags:
- nextjs
- nextjs-16
---

# Next.js 16

- Async `params`/`cookies`/`headers`; opt-in caching via `"use cache"`; Turbopack default.

Anti-patterns:

- ❌ Sync request APIs; ✅ `await` `params`, `cookies()`, and `headers()`.
- ❌ Keep `middleware.ts`; ✅ use `proxy.ts` and `export function proxy`.
- ❌ `revalidateTag("posts")`; ✅ `revalidateTag("posts", "max")` or `{ expire: ... }`.

References: `references/migration-checklist.md`, `references/cache-components.md`, `references/turbopack.md`