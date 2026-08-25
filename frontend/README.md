# Signalis frontend

React + TypeScript client for Signalis. See
[../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the system-level
picture and [../README.md](../README.md) for full first-time setup.

## Directory layout

```
src/
  api/
    client.ts      Axios instance, response interceptor, ApiError class
                    (carries the HTTP status so callers can distinguish a
                    confirmed 404 from a transient network/server failure).
    endpoints.ts    Typed endpoint functions grouped by resource
                    (dashboardApi, leadsApi, rankingApi, ...).
  components/
    ui/             shadcn-pattern primitives built on Radix (badge,
                     button, card, dialog, select, tabs, toast, ...) —
                     owned in this codebase, not an installed package.
    layout/          AppShell: navigation and page frame.
    leads/           Domain-specific presentational components
                     (StageBadge, ConfidenceMeter).
  pages/            One file per application screen: Dashboard, Lead
                    Pipeline, Lead Detail, Data Sources, Setup.
  lib/utils.ts      cn() class-merge helper.
  types/api.ts      TypeScript interfaces mirroring the backend's Pydantic
                    schemas.
  index.css         Design tokens (CSS custom properties) and global
                    styles.
  App.tsx           Route definitions.
  main.tsx          Entry point.
```

## Setup

```bash
npm install
```

## Running

```bash
npm run dev
```

Served at `http://localhost:5173` by default. Requires the backend running
at the URL configured via `VITE_API_BASE_URL` (defaults to
`http://localhost:8000`) — see [../README.md](../README.md) for backend
setup. The backend's CORS allowlist
(`backend/app/core/config.py::cors_allow_origins`) must include whichever
origin Vite actually serves from; if that port is already in use, Vite
moves to the next one, and requests will fail with a CORS error rather than
a clear message until the allowlist or the port matches.

## Building

```bash
npm run build    # tsc -b && vite build
npm run preview  # serve the production build locally
```

## Linting and type checking

```bash
npx eslint .
npx tsc --noEmit
```

## Conventions

- **Loading, error, and empty states are three distinct branches**, not
  two. A `useQuery` that only checks `isLoading` and falls through to a
  bare "not found" or "no data" message on any other outcome will
  misreport a transient failure as a permanent one — see `DashboardPage.tsx`
  and `LeadDetailPage.tsx` for the established pattern (check `error`
  against `ApiError`'s `status` field, not just its presence).
- **Colors and typography come from `index.css` design tokens**
  (`hsl(var(--token))`), not raw Tailwind palette classes or hardcoded hex
  values — this keeps chart colors, badge variants, and one-off accents
  from drifting out of sync with the rest of the theme.
- **Path imports use the `@/` alias** (`@/components/ui/button`, not a
  relative `../../components/ui/button`), configured in `vite.config.ts`
  and `tsconfig.json`.
