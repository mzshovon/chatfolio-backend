# Required API — endpoints the frontend needs that don't exist yet

This is a gap list, not a spec for the whole app. It only covers what's missing —
every screen currently backed by a real, working endpoint (auth, profile, CV upload,
portfolio sections, publish settings, conversations, and the admin users/chatfolios/
metrics/cv-jobs list+action endpoints already in
[`ADMIN_PANEL_UI_REFERENCE.md`](./ADMIN_PANEL_UI_REFERENCE.md)) is **excluded** here on
purpose. Where a section below is marked "preview-only" in the app today, that's the
exact reason: the UI and interaction exist (`PreviewBanner` in the relevant page says
so explicitly), but there's nothing to call.

Conventions below match the existing doc: `/api/v1` prefix, plain-array responses for
lists (no wrapper object), `limit`/`offset` query params for pagination, `{"detail":
"..."}` error shape, `Authorization: Bearer <access_token>` on every authenticated call.

---

## 1. Candidate — Account settings

Currently the dashboard header's "Account settings" menu item is inert (see
`src/components/dashboard/header.tsx`). There's no page for it because there's nothing
it could safely do yet — changing your password or login email needs its own guarded
flow, distinct from `/profiles/me` (which only covers portfolio-facing fields like
`contact_email`, not the account's actual login credentials).

### `POST /api/v1/auth/change-password` — requires login, suggest 5/min per IP

```jsonc
// Request
{ "current_password": "supersecret123", "new_password": "brandnewpass456" } // new: 8-128 chars

// 204 No Content
```
`401` if `current_password` doesn't match. `422` if `new_password` fails the length
check. Unlike the forgot/reset flow (§2.4), this doesn't need email delivery — it's an
authenticated user proving they already know the old password. Suggest **not**
revoking other sessions' refresh tokens on this action (unlike a forced reset via
§2.4) unless that's a deliberate security decision worth documenting either way.

### `PATCH /api/v1/auth/change-email` — requires login, suggest 5/min per IP

```jsonc
// Request
{ "new_email": "ada+new@example.com", "password": "supersecret123" }

// 200 OK
{ "id": "uuid", "email": "ada+new@example.com", "role": "candidate", "is_active": true }
```
`401` if `password` is wrong. `409` if `new_email` is already registered. Whether this
requires a confirmation-link step (verify the new address before it takes effect) is a
product decision — if so, this becomes two calls (`request-email-change` +
`confirm-email-change?token=...`) instead of one; either way, note it here so the
frontend knows which shape to build against.

**Frontend consumer**: a new `/dashboard/settings` page, linked from the header menu
item that's currently disabled in both `src/components/dashboard/header.tsx` and
`src/components/admin/header.tsx`.

---

## 2. Candidate — Dashboard analytics

The candidate dashboard home (`src/app/dashboard/page.tsx`) shows "Portfolio visitors"
and "AI tokens used" as fixed static numbers today, because no per-candidate analytics
endpoint exists — only the admin's site-wide `GET /api/v1/admin/metrics` does.

### `GET /api/v1/dashboard/analytics`

```jsonc
// 200 OK
{
  "portfolio_visitors_total": 1284,
  "portfolio_visitors_delta_pct": 18,       // vs. the prior equivalent period; sign matters
  "ai_tokens_used": 412000,
  "ai_tokens_monthly_quota": 1000000
}
```
No pagination needed — this is a single point-in-time snapshot per candidate, same
spirit as `GET /api/v1/admin/metrics`. If visitor counts need a time dimension later (a
chart, not just a headline number), that's a separate, explicitly time-series
endpoint — don't overload this one with a `range` param unless it's actually built.

**Frontend consumer**: the two static `STATIC_STATS` entries in
`src/app/dashboard/page.tsx`.

---

## 3. Admin — User management (create / edit / ban / delete)

Docs §8 only defines `GET /api/v1/admin/users` (list). The Users page
(`src/app/admin/users/page.tsx`), Add User page (`src/app/admin/users/add/page.tsx`),
and Edit User page (`src/app/admin/users/[id]/edit/page.tsx`) are all built and
interactive, but every mutating action on them is explicitly local-only and resets on
reload — there's no endpoint for any of this yet.

### `GET /api/v1/admin/users/{id}` — single user, for the Edit User page

```jsonc
// 200 OK
{ "id": "uuid", "email": "ada@example.com", "role": "candidate", "is_active": true }
```
Same shape as a list row. Without this, the Edit User page can only prefill what was
passed via query string from the row that linked to it (see
`src/app/admin/users/[id]/edit/edit-user-view.tsx`) — it has no way to independently
load a user by just the `id` in the URL, e.g. on a direct link or reload.

### `POST /api/v1/admin/users` — create (the Add User page)

```jsonc
// Request
{ "email": "new@example.com", "password": "temporarypass123", "role": "candidate", "is_active": true }
// role: "candidate" | "admin"

// 201 Created
{ "id": "uuid", "email": "new@example.com", "role": "candidate", "is_active": true }
```
`409` if the email is already registered. Whether the created user gets a real
temp-password email or must reset via §2.4 on first login is a product decision worth
pinning down — the Add User form currently collects a "temporary password" field
speculatively, matching the template design; drop it if the backend will always issue
one server-side instead.

### `PATCH /api/v1/admin/users/{id}` — edit / ban / role change

```jsonc
// Request — every field optional, send only what changed
{ "email": "ada@example.com", "role": "admin", "is_active": false }

// 200 OK — full updated row
{ "id": "uuid", "email": "ada@example.com", "role": "admin", "is_active": false }
```
This one endpoint covers three UI actions that are currently separate local-only
buttons (Edit, Ban/Unban) — `is_active: false` is exactly what "Ban" toggles today in
`src/app/admin/users/page.tsx`, just without persistence.

### `DELETE /api/v1/admin/users/{id}`

```jsonc
// 204 No Content
```
Whether this is a hard delete or a soft one (matches `is_active: false` in practice?)
is worth clarifying — if soft-delete and ban end up being the same operation
server-side, the frontend's separate Ban/Delete buttons should probably collapse into
one, but that's a UI follow-up once the real behavior is known.

**Suggested rate limit**: 20/min per IP on the mutating three, consistent with other
admin write actions (`POST /admin/chatfolios/{id}/unpublish`, `POST
/admin/cv-jobs/{id}/retry` have no explicit limit per §10 today, but user creation
touches auth and probably should be limited more like `/auth/register`).

**Frontend consumers**: `src/app/admin/users/page.tsx` (list actions),
`src/app/admin/users/add/page.tsx`, `src/app/admin/users/[id]/edit/edit-user-view.tsx`.

---

## 4. Admin — Roles

No roles system exists in the backend at all today — a user's `role` is a fixed field
returned by `/auth/me` and the admin users list, not a manageable entity. The Roles
page (`src/app/admin/roles/page.tsx`) is fully interactive but entirely local state,
matching the template's own original mock behavior; nothing here syncs anywhere.

### `GET /api/v1/admin/roles?limit=20&offset=0`

```jsonc
// 200 OK — plain array, same pagination convention as every other admin list
[
  { "id": "uuid", "name": "Admin", "description": "Full access to all admin views and actions.",
    "permissions": ["users.view", "users.manage", "roles.manage"] }
]
```

### `POST /api/v1/admin/roles`

```jsonc
// Request
{ "name": "Reviewer", "description": "Read-only access to chatfolios and metrics.",
  "permissions": ["chatfolios.view", "metrics.view"] }

// 201 Created — same shape plus id
{ "id": "uuid", "name": "Reviewer", "description": "Read-only access to chatfolios and metrics.",
  "permissions": ["chatfolios.view", "metrics.view"] }
```
`409` if `name` collides with an existing role.

### `PATCH /api/v1/admin/roles/{id}` — every field optional

```jsonc
// Request
{ "permissions": ["chatfolios.view", "metrics.view", "cvjobs.retry"] }

// 200 OK — full updated row
```

### `DELETE /api/v1/admin/roles/{id}`

```jsonc
// 204 No Content
```
What happens to users currently assigned this role is the important open question —
reassign to a default role, block the delete with `409` while any user holds it, or
allow an orphaned role string on those users? The confirm dialog in
`src/app/admin/roles/page.tsx` already warns "users assigned to this role will need a
new role," matching the template's assumption, but the actual backend behavior should
decide whether that warning is accurate.

**Frontend consumer**: `src/app/admin/roles/page.tsx` in full — form, list, and delete
confirmation are all built, just pointed at local state instead of these endpoints.

---

## 5. Admin — Permissions

Same situation as Roles: no permissions system exists server-side. The Permissions
page (`src/app/admin/permissions/page.tsx`) is a local-only CRUD table of
`key`/`description`/`used-by-N-roles` rows.

### `GET /api/v1/admin/permissions?limit=20&offset=0`

```jsonc
[
  { "id": "uuid", "key": "users.view", "description": "View the users list.",
    "used_by_roles_count": 3 }
]
```
`used_by_roles_count` is a derived/computed field (how many roles currently include
this key in their `permissions` array) — it exists purely for the "Used by" column and
doesn't need to be stored, just computed at read time.

### `POST /api/v1/admin/permissions`

```jsonc
// Request
{ "key": "chatfolios.unpublish", "description": "Unpublish any chatfolio." }

// 201 Created
{ "id": "uuid", "key": "chatfolios.unpublish", "description": "Unpublish any chatfolio.",
  "used_by_roles_count": 0 }
```
`422` if `key` doesn't match a `word.word` pattern (the template enforces this loosely
client-side; worth a real server-side check too). `409` if `key` already exists.

### `PATCH /api/v1/admin/permissions/{id}`

```jsonc
// Request — every field optional
{ "description": "Unpublish any candidate's chatfolio." }

// 200 OK — full updated row
```
Note: `key` is probably safer treated as immutable after creation (it's what roles
reference) — if the backend allows renaming it, every role's `permissions` array
referencing the old key needs to be migrated atomically, which is a real design
decision, not just a validation rule.

### `DELETE /api/v1/admin/permissions/{id}`

```jsonc
// 204 No Content
```
Same open question as deleting a role: does this silently strip the key from every
role that granted it, or block while `used_by_roles_count > 0`? The confirm dialog in
`src/app/admin/permissions/page.tsx` currently assumes the former ("will be removed
from any roles that grant it").

**Frontend consumer**: `src/app/admin/permissions/page.tsx` in full.

---

## 6. Admin — Platform analytics extension

The admin dashboard home (`src/app/admin/page.tsx`) shows "Total visitors" and "AI
tokens used" as fixed static numbers — `GET /api/v1/admin/metrics` (already real, already
wired) doesn't include them; it only has the plain counts documented in §8
(`total_users`, `total_candidates`, `published_chatfolios`, chat/CV counts).

Two ways to close this gap — pick whichever fits the backend's actual data model
better, this isn't prescriptive:

**Option A** — extend the existing endpoint:
```jsonc
// GET /api/v1/admin/metrics — additive fields, nothing existing changes shape
{
  "total_users": 42, "total_candidates": 40, "published_chatfolios": 18,
  "total_chat_sessions": 310, "total_chat_messages": 1204, "flagged_chat_sessions": 3,
  "cv_parse_success_count": 55, "cv_parse_failed_count": 4,
  "total_portfolio_visitors": 18432,
  "recruiters_engaged": 612,
  "ai_tokens_used": 4200000,
  "ai_tokens_monthly_quota": 10000000
}
```

**Option B** — a separate endpoint, if these numbers come from a different, slower, or
less-real-time data source than the rest of `/admin/metrics`:
```jsonc
// GET /api/v1/admin/analytics
{ "total_portfolio_visitors": 18432, "recruiters_engaged": 612,
  "ai_tokens_used": 4200000, "ai_tokens_monthly_quota": 10000000 }
```
Either way: no pagination needed (point-in-time snapshot, same as `/admin/metrics`
today, per §8's own note that it's "plain point-in-time counts, no time-series/history
— poll on dashboard load, not a live feed").

**Frontend consumer**: the `STATIC_STATS` array in `src/app/admin/page.tsx`.

---

## Summary table

| Section | Endpoint(s) needed | Paginated? | Frontend file |
|---|---|---|---|
| Candidate account settings | `POST /auth/change-password`, `PATCH /auth/change-email` | No | new `/dashboard/settings` page |
| Candidate dashboard analytics | `GET /dashboard/analytics` | No | `src/app/dashboard/page.tsx` |
| Admin user management | `GET/POST/PATCH/DELETE /admin/users(/{id})` | List only (`limit`/`offset`) | `src/app/admin/users/**` |
| Admin roles | `GET/POST/PATCH/DELETE /admin/roles(/{id})` | List only (`limit`/`offset`) | `src/app/admin/roles/page.tsx` |
| Admin permissions | `GET/POST/PATCH/DELETE /admin/permissions(/{id})` | List only (`limit`/`offset`) | `src/app/admin/permissions/page.tsx` |
| Admin platform analytics | `GET /admin/metrics` (extended) or `GET /admin/analytics` | No | `src/app/admin/page.tsx` |
