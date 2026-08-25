# Admin Panel UI — Frontend Reference

Backend reference for the **authenticated** frontend: one login system, two views gated by
role. Every candidate who registers can manage their own portfolio (§3-§7 below); a user whose
`role` is `admin` additionally sees the internal ops views (§8). This is deliberately one app
with role-based routing, not two separate apps — there's one JWT, one `/auth/me` call, and the
`role` field on the response tells you which UI to render.

Companion doc: [`PUBLIC_CHAT_UI_REFERENCE.md`](./PUBLIC_CHAT_UI_REFERENCE.md) covers the
unauthenticated recruiter-facing side. Full backend design: [`BACKEND_PLAN.md`](./BACKEND_PLAN.md).

---

## 1. Base URL & conventions

- All endpoints prefixed `/v1`.
- `Content-Type: application/json` on every request with a body, except file upload (§4, uses
  `multipart/form-data`).
- Authenticated endpoints require `Authorization: Bearer <access_token>`.
- Errors: `{"detail": "message"}` with a non-2xx status. No machine-readable error code field —
  branch on HTTP status.
- Every "owned resource" endpoint (`/profiles/me/...`, `/cv/...`, `/sections/...`, etc.) is
  scoped to the logged-in user automatically from the token — there's never an explicit
  `user_id`/`profile_id` in the URL or body for candidate-side calls. You cannot accidentally
  (or deliberately) address another candidate's data through these routes; the backend enforces
  ownership on every one, and returns `404` (not `403`) for a resource that exists but isn't
  yours — don't rely on the distinction to infer whether an ID is valid.

---

## 2. Auth

### `POST /v1/auth/register` — 5/min per IP

```jsonc
// Request
{ "email": "ada@example.com", "password": "supersecret123" }  // password: 8-128 chars

// 201 Created
{ "id": "uuid", "email": "ada@example.com", "role": "candidate", "is_active": true }
```
`409` if the email is already registered.

### `POST /v1/auth/login` — 10/min per IP

```jsonc
// Request
{ "email": "ada@example.com", "password": "supersecret123" }

// 200 OK — account does NOT have 2FA enabled
{ "requires_two_factor": false, "access_token": "eyJ...", "refresh_token": "abc123...", "token_type": "bearer" }

// 200 OK — account HAS 2FA enabled: no tokens yet, a verification code was just sent
{
  "requires_two_factor": true,
  "challenge_token": "eyJ...",
  "method": "email",                          // "email" | "phone" | "both"
  "masked_destinations": ["a•••@example.com"]  // 2 entries for "both"
}
```
`401` for wrong email/password (same message for both — don't build UI that distinguishes them).
Branch the frontend on the `requires_two_factor` field, not on HTTP status — both shapes are
`200`, since needing a second factor is a normal step in login, not an error. See §2.4 for the
full 2FA verification flow.

### `POST /v1/auth/refresh` — 20/min per IP

```jsonc
// Request
{ "refresh_token": "abc123..." }

// 200 OK — a NEW pair; the old refresh token is now revoked
{ "access_token": "eyJ...", "refresh_token": "def456...", "token_type": "bearer" }
```
`401` if the token is invalid, expired, or **already used once** — refresh tokens rotate on
every use and a reused one is rejected outright (this is a theft/replay detector, not a bug: if
your app ever gets a `401` here unexpectedly, it means two tabs/requests raced to refresh with
the same token — see §2.3).

### `POST /v1/auth/logout` — 20/min per IP

```jsonc
// Request
{ "refresh_token": "abc123..." }
// 204 No Content — revokes the token if it wasn't already
```

### `GET /v1/auth/me` — no rate limit beyond the norm, needs `Authorization`

```jsonc
// 200 OK
{ "id": "uuid", "email": "ada@example.com", "role": "candidate", "is_active": true }
```
Call this once after login/on app load to get `role` and decide candidate vs admin UI. `401` if
the token is missing/expired/invalid.

### 2.1 Token lifetimes

- Access token: **15 minutes**.
- Refresh token: **30 days**, single-use (rotates every refresh call).

### 2.2 Where to store tokens — read this before writing any storage code

The backend returns both tokens as plain JSON fields — it does **not** set any cookie. That
means storage safety is entirely the frontend's responsibility, and the wrong choice here is a
real vulnerability, not a style preference:

- **Access token: keep it in memory only** (a JS variable / state store). Never `localStorage`,
  never `sessionStorage`, never a non-httpOnly cookie. It's short-lived (15 min) specifically so
  that if it does leak, the exposure window is small.
- **Refresh token: this is the sensitive one** — it's valid for 30 days and grants a fresh
  access token on demand. `localStorage`/`sessionStorage` are readable by any script running on
  the page, so any XSS vulnerability anywhere on the site becomes a full account takeover if the
  refresh token lives there. Until a cookie-based flow exists on the backend (it doesn't yet —
  see `BACKEND_PLAN.md` §10), the least-bad options, in order of preference, are:
  1. Keep the refresh token in memory too, and accept that a full page reload requires a fresh
     login. Simplest, safest, worst UX.
  2. If "stay logged in across reloads" is a hard requirement, store it in memory but persist
     the *fact that a session existed* (not the token) so you can prompt a silent re-auth flow —
     genuinely depends on what auth infrastructure the frontend has available; there's no
     one-size-fits-right answer here without knowing that.
  3. Do not reach for `localStorage` as the default "it's fine, everyone does it" choice for
     this specific token — it's the one credential in this whole API with a 30-day blast radius.

### 2.3 Refresh token races

Because refresh tokens are single-use, two concurrent requests both triggering "my access token
expired, let me refresh" will race: one wins and gets a new pair, the other gets `401` on an
already-revoked token. Guard against this with a single in-flight refresh promise that all
callers await (a standard "refresh mutex" pattern) rather than letting every failed request
independently call `/auth/refresh`.

### 2.4 Forgot / reset password

Two calls, no login required for either.

#### `POST /v1/auth/forgot-password` — 5/min per IP

```jsonc
// Request
{ "email": "ada@example.com" }
// 204 No Content — ALWAYS, whether or not that email is registered
```
This always returns `204` regardless of whether the email exists — the backend deliberately
never reveals which emails are registered. Show the same generic "if an account exists, we've
sent a reset link" message every time; don't build a UI path for "email not found" here.

If the email is registered, this sends a real email with a link shaped like
`{FRONTEND_BASE_URL}/reset-password?token=<opaque-token>` — build a page at that route that reads
`token` from the query string and collects a new password (this is the "Set a new password"
screen: new + confirm fields, submitted together to the endpoint below). The token expires in
**30 minutes** and is single-use.

#### `POST /v1/auth/reset-password` — 5/min per IP

```jsonc
// Request
{ "token": "the-token-from-the-query-string", "new_password": "brandnewpass123" }  // 8-128 chars
// 204 No Content
```
`401` if the token is invalid, expired, or already used — show "this reset link is no longer
valid, request a new one" and route back to the forgot-password screen; don't try to distinguish
"expired" from "already used" from "never existed" in the UI, the backend doesn't either.

**Do the "new password" / "confirm password" match check client-side** — the API takes one
`new_password` field, not two; there's no server-side confirm-field comparison to fall back on.

A successful reset silently revokes every refresh token the account had outstanding (every other
logged-in device/tab is signed out on its next `/auth/refresh` call). There's no separate
"log out everywhere" action because this already is one.

### 2.5 Two-factor authentication — enrollment (requires login)

Enrolling is a two-step confirm flow: request a code, then prove you received it. All calls here
need `Authorization` — this is an account-settings action, not part of the public login screen.

#### `POST /v1/auth/2fa/setup` — 5/min per IP

```jsonc
// Request — method "email"
{ "method": "email" }

// Request — method "phone" (phone required the first time; omit it once one's on file)
{ "method": "phone", "phone": "+15551234567" }

// Request — method "both"
{ "method": "both", "phone": "+15551234567" }

// 200 OK
{ "method": "email", "masked_destinations": ["a•••@example.com"] }
```
Sends a 6-digit code to the chosen channel(s) immediately (same code to both destinations for
`"both"`) — 2FA is **not** enabled yet at this point, only pending. `422` if `method` is `"phone"`
or `"both"` and no phone is on file and none was supplied in this call.

#### `POST /v1/auth/2fa/verify-setup` — 10/min per IP

```jsonc
// Request
{ "code": "123456" }
// 204 No Content — 2FA is now enabled on the account
```
`401` for a wrong or expired code (10-minute expiry, 5 attempts before the code is dead and a new
`/2fa/setup` call is required). Once this succeeds, every future `/auth/login` for this account
returns the challenge shape from §2.3 instead of tokens directly.

There's no "disable 2FA" or "change method" endpoint yet — re-running `/2fa/setup` with a
different method mid-session is not currently supported; treat 2FA as set-once for now and note
this as a gap if the UI needs to offer turning it off.

### 2.6 Two-factor authentication — login verification (no login required)

This is the second step after a `/auth/login` call returned `requires_two_factor: true` (§2.3).

#### `POST /v1/auth/2fa/login/verify` — 10/min per IP

```jsonc
// Request
{ "challenge_token": "eyJ...", "code": "123456" }
// 200 OK
{ "requires_two_factor": false, "access_token": "eyJ...", "refresh_token": "abc123...", "token_type": "bearer" }
```
`401` for a wrong code, an expired code (10 min), or an expired challenge token (**5 minutes** —
noticeably shorter than the OTP itself, since this token only bridges "password already
verified" to "OTP verified"; if a user stalls on the code-entry screen past 5 minutes, send them
back to `/auth/login` to start over rather than trying to resend against a dead challenge).

#### `POST /v1/auth/2fa/login/resend` — 3/min per IP

```jsonc
// Request
{ "challenge_token": "eyJ..." }
// 204 No Content — the previous code is now dead; only the new one works
```
Use this for the code screen's "Didn't get a code? Resend" link. Resending invalidates whatever
code was sent before it, so a stale "verify" call using the old code after a resend correctly
gets `401` — don't treat that as a bug if you see it while testing.

---

## 3. Candidate profile

All under `/v1/profiles/me`, all require `Authorization`. The profile is auto-created on first
access — there's no separate "create profile" step; `GET`/`PATCH` both lazily create one if it
doesn't exist yet.

### `GET /v1/profiles/me` · `PATCH /v1/profiles/me`

```jsonc
// PATCH request — every field optional, send only what changed
{ "full_name": "Ada Lovelace", "title": "Backend Engineer", "bio": "...", "location": "...",
  "contact_email": "ada@example.com", "phone": "...", "social_links": { "github": "..." } }

// 200 OK (both GET and PATCH)
{ "id": "uuid", "full_name": "Ada Lovelace", "title": "Backend Engineer", "bio": null,
  "location": null, "contact_email": null, "phone": null, "social_links": {},
  "status": "draft" }   // status: "draft" | "approved" — flips once published (see §6)
```

### Child resources: Experience / Project / Skill / Education

Same CRUD shape for all four, under `/v1/profiles/me/{experience|projects|skills|education}`.
**There is no single-item GET** — only list, create, update, delete. Fetch the full list and find
the item client-side if you need to re-read one after a list is already loaded; don't build a UI
flow that assumes a per-item fetch exists.

- `GET /v1/profiles/me/experience` → `200`, full array for the current user (no pagination — the
  expected cardinality per candidate is small, tens at most)
- `POST /v1/profiles/me/experience` → `201`, body per-type (see table below)
- `PATCH /v1/profiles/me/experience/{id}` → `200`, all fields optional
- `DELETE /v1/profiles/me/experience/{id}` → `204`

| Type | Fields |
|---|---|
| `experience` | `company` (str, required), `role` (str, required), `start_date`/`end_date` (ISO date, nullable), `is_current` (bool, default false), `description` (str, nullable) |
| `projects` | `title` (str, required), `description` (str, nullable), `tech_stack` (`string[]`, default `[]`), `impact` (str, nullable), `links` (`{[key: string]: string}`, default `{}`) |
| `skills` | `name` (str, required), `category` (str, nullable), `proficiency` (str, nullable — free text, e.g. "Excellent"/"Interim", not an enum) |
| `education` | `institution` (str, required), `degree`/`field` (str, nullable), `start_date`/`end_date` (ISO date, nullable) |

**Response samples** — every response echoes the request body plus a server-assigned `id`;
`PATCH` returns the same shape with only the fields you sent updated (send only what changed,
same as `PATCH /profiles/me`). `POST` responses are `201`, `PATCH` are `200`.

```jsonc
// POST /v1/profiles/me/experience
// Request
{ "company": "Acme", "role": "Backend Engineer", "start_date": "2022-08-23", "end_date": null,
  "is_current": true, "description": "Leading backend platform work." }

// 201 Created
{ "company": "Acme", "role": "Backend Engineer", "start_date": "2022-08-23",
  "end_date": null, "is_current": true, "description": "Leading backend platform work.",
  "id": "uuid" }
```

```jsonc
// POST /v1/profiles/me/projects
// Request
{ "title": "CFL", "description": "Closed feedback loop system automating survey processes.",
  "tech_stack": ["Laravel", "PHP", "MySQL", "ReactJS"], "impact": "20M+ BDT annually",
  "links": { "repo": "https://github.com/..." } }

// 201 Created
{ "title": "CFL", "description": "Closed feedback loop system automating survey processes.",
  "tech_stack": ["Laravel", "PHP", "MySQL", "ReactJS"], "impact": "20M+ BDT annually",
  "links": { "repo": "https://github.com/..." }, "id": "uuid" }
```

```jsonc
// POST /v1/profiles/me/skills
// Request
{ "name": "PHP", "category": "Language", "proficiency": "Best" }

// 201 Created
{ "name": "PHP", "category": "Language", "proficiency": "Best", "id": "uuid" }
```

```jsonc
// POST /v1/profiles/me/education
// Request
{ "institution": "University of Dhaka", "degree": "BSc", "field": "Computer Science",
  "start_date": "2016-09-01", "end_date": "2020-06-01" }

// 201 Created
{ "institution": "University of Dhaka", "degree": "BSc", "field": "Computer Science",
  "start_date": "2016-09-01", "end_date": "2020-06-01", "id": "uuid" }
```

```jsonc
// PATCH /v1/profiles/me/skills/{id} — every field optional, send only what changed
// Request
{ "proficiency": "Excellent" }

// 200 OK — unchanged fields keep their existing values, not reset to defaults
{ "name": "PHP", "category": "Language", "proficiency": "Excellent", "id": "uuid" }
```

`GET /v1/profiles/me/{path}` (the list endpoint) returns a plain array of the same per-type shape
shown above — `[{ ...experience }, { ...experience }]`, no wrapper object, no pagination metadata.

**Every create/update/delete on these four types silently triggers a background re-embed** of
that item for chat retrieval (§1 of the public chat doc) — no action needed from the UI, just be
aware there's a short (usually sub-second, worker-dependent) delay before an edit is reflected
in what the chat widget can answer about.

---

## 4. CV upload — 10/hour per IP on upload and retry

```jsonc
// POST /v1/cv/upload — multipart/form-data, field name "file"
// Accepts PDF, DOC, DOCX only, 20MB max. The backend also verifies the actual file signature,
// not just the extension/content-type — a mislabeled file is rejected with 422 even if the
// extension looks right.

// 201 Created
{ "id": "uuid", "status": "pending", "file_type": "pdf", "size_bytes": 123456,
  "error_message": null, "parsed_json": null }
```

`status` progresses `pending` → `processing` → `parsed` | `failed` via a background worker — the
frontend must **poll** `GET /v1/cv/{id}/status` (same response shape) to see this transition;
there's no push/webhook. A few-second interval is reasonable; the parse itself typically takes a
few seconds to under a minute depending on LLM latency.

`POST /v1/cv/{id}/retry` → same response shape, only valid when `status == "failed"` (`422`
otherwise). Re-runs the parse.

---

## 5. Portfolio sections (AI-generated intro/summary)

`GET /v1/sections` — returns exactly two sections (`intro`, `summary`), auto-generating any that
don't exist yet on first call (so the very first call after profile setup may take a few seconds
— show a loading state, not just a spinner-less blank).

```jsonc
[
  { "id": "uuid", "section_type": "intro", "content": "...", "status": "draft",
    "generated_by": "ai", "version": 1 },
  { "id": "uuid", "section_type": "summary", "content": "...", "status": "draft",
    "generated_by": "ai", "version": 1 }
]
```

- `PATCH /v1/sections/{id}` `{ "content": "..." }` — manual edit. Sets `generated_by: "manual"`
  and resets `status` back to `"draft"` even if it was previously approved — a manual edit always
  needs re-approval before it grounds chat answers again.
- `POST /v1/sections/{id}/regenerate` — **10/hour per IP**, re-runs AI generation, bumps
  `version`. Disable the button after a few uses per session rather than letting a user
  discover the rate limit via a raw `429` — show remaining-attempts UX if you track it
  client-side, or just a friendly "you've regenerated this a lot recently, try again in a bit."
- `POST /v1/sections/{id}/approve` — flips `status` to `"approved"` and enqueues it for chat
  retrieval. **Both sections must be approved before the portfolio can be published** (§6).

---

## 6. Portfolio settings & publish

All under `/v1/portfolio-settings`, auto-creates on first access (same lazy pattern as §3). A
freshly auto-created record (no profile name or slug chosen yet) looks like this — `slug`
defaults to `candidate-<random>`, `contact_cta_config` defaults to `{}`, not a pre-filled example:

```jsonc
// GET /v1/portfolio-settings
{ "slug": "candidate-f391c9", "subdomain": "candidate-f391c9.chatfolio.com",
  "previous_slug": null, "is_published": false, "published_at": null,
  "contact_cta_config": {}, "cv_downloadable": true }
```

`slug` pattern: lowercase letters/digits/hyphens, 3-63 chars, can't start/end with a hyphen
(`^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$`) — validate client-side before submit for a good error
UX; the backend also enforces it (`422`) and uniqueness (`409` if taken).

```jsonc
// PATCH /v1/portfolio-settings — every field optional, send only what changed
// Request
{ "slug": "portfolio-doc-test",
  "contact_cta_config": { "label": "Get in touch", "url": "mailto:test@example.com" } }

// 200 OK
{ "slug": "portfolio-doc-test", "subdomain": "portfolio-doc-test.chatfolio.com",
  "previous_slug": "candidate-f391c9", "is_published": false, "published_at": null,
  "contact_cta_config": { "label": "Get in touch", "url": "mailto:test@example.com" },
  "cv_downloadable": true }
```

Note `previous_slug` picks up the slug you just replaced automatically — you never send it
yourself, and it's what powers the "old link keeps working" redirect below.

### `POST /v1/portfolio-settings/publish`

**Preconditions, both required or `422` with a combined message covering everything missing at
once** (verified live — don't assume it stops at the first failure):

```jsonc
// 422 — nothing set yet
{ "detail": "Approve these sections before publishing: intro, summary. Add your full name before publishing." }

// 422 — name is set, sections still aren't approved
{ "detail": "Approve these sections before publishing: intro, summary." }
```

Surface this as a checklist in the UI ("2 of 2 sections approved ✓ / name set ✓") rather than
only showing the error after a failed publish attempt. Once both preconditions are met:

```jsonc
// 200 OK
{ "slug": "portfolio-doc-test", "subdomain": "portfolio-doc-test.chatfolio.com",
  "previous_slug": "candidate-f391c9", "is_published": true,
  "published_at": "2026-08-22T23:59:30.428571Z",
  "contact_cta_config": { "label": "Get in touch", "url": "mailto:test@example.com" },
  "cv_downloadable": true }
```

### `POST /v1/portfolio-settings/unpublish`

Always succeeds, takes the page down immediately (`is_published: false`). **`published_at` is
*not* cleared** — it keeps the timestamp of the last publish rather than resetting to `null`, so
don't use `published_at == null` as your "is/was this ever published" check; use `is_published`
for current state and treat `published_at` as "last published at," full stop:

```jsonc
// 200 OK
{ "slug": "portfolio-doc-test", "subdomain": "portfolio-doc-test.chatfolio.com",
  "previous_slug": "candidate-f391c9", "is_published": false,
  "published_at": "2026-08-22T23:59:30.428571Z",
  "contact_cta_config": { "label": "Get in touch", "url": "mailto:test@example.com" },
  "cv_downloadable": true }
```

Renaming the slug after publishing keeps the **old** slug working as a `307` redirect (see the
public chat doc §2) — worth mentioning in the UI ("your old link will keep working") so
candidates aren't afraid to rename.

---

## 7. Candidate dashboard — recruiter conversations

Read-only views of chats recruiters have had, under `/v1/dashboard`.

### `GET /v1/dashboard/conversations?limit=20&offset=0`

```jsonc
[
  { "id": "uuid", "started_at": "2026-08-21T11:30:56Z", "last_active_at": "2026-08-21T11:34:04Z",
    "is_flagged": false, "reviewed_by_candidate": false, "message_count": 6,
    "recruiter_metadata": {                    // null if the recruiter never volunteered any of this
      "name": "Rafi", "company": "Cefalo", "role": "Backend Engineer",
      "required_skills": "PHP, Golang", "experience_expectation": null,
      "location_pref": null, "timeline": null } }
]
```

`is_flagged` means the session tripped the abuse/rate-limit threshold (repeated rapid-fire
messages) — worth a visual badge so the candidate knows to treat that transcript with more
scrutiny, not necessarily anything actionable beyond awareness.

Only sessions where at least one message was actually sent are listed — a recruiter who opens
the chat widget (which creates a session, see `PUBLIC_CHAT_UI_REFERENCE.md` §3) but never types
anything never shows up here, so `message_count` is never `0` in this list. Don't build UI for an
"empty conversation" state; it can't happen.

### `GET /v1/dashboard/conversations/{id}`

Same shape plus `"messages": [{ "role": "recruiter"|"assistant", "content": "...", "intent":
"skill_inquiry"|"unknown", "created_at": "..." }]`, ordered oldest-first. `intent` is the
classified intent of that turn's recruiter message, mirrored onto both the recruiter message and
the assistant reply that answered it — so it's on every message, not just the recruiter one
(older conversations recorded before 2026-08-22 may still show `null` on their assistant rows;
only new messages carry it on both).

### `POST /v1/dashboard/conversations/{id}/mark-reviewed`

No body. Returns the summary shape with `reviewed_by_candidate: true`. Purely a candidate-side
bookkeeping flag ("I've read this") — has no effect on anything else.

---

## 8. Admin-only views

Everything in this section additionally requires the logged-in user's `role` (from `/auth/me`)
to be `"admin"` — there's no self-service way for a candidate to become one (see
`BACKEND_PLAN.md`'s Phase 12 notes if you need to promote one manually for testing). Every
endpoint here 403s for a non-admin token. Gate these routes/nav items on `role === "admin"`
client-side too, but treat that as a UX nicety, not the security boundary — the backend enforces
it regardless.

### `GET /v1/admin/users?limit=20&offset=0`
```jsonc
[{ "id": "uuid", "email": "ada@example.com", "role": "candidate", "is_active": true }]
```

### `GET /v1/admin/chatfolios?is_published=true&limit=20&offset=0`
`is_published` query param is optional — omit it to see both published and unpublished.
```jsonc
[{ "id": "uuid", "slug": "ada-lovelace", "is_published": true,
   "published_at": "2026-08-21T14:30:59Z", "owner_email": "ada@example.com" }]
```

### `GET /v1/admin/metrics`
```jsonc
{ "total_users": 42, "total_candidates": 40, "published_chatfolios": 18,
  "total_chat_sessions": 310, "total_chat_messages": 1204, "flagged_chat_sessions": 3,
  "cv_parse_success_count": 55, "cv_parse_failed_count": 4 }
```
Plain point-in-time counts, no time-series/history — poll on dashboard load, not a live feed.

### `GET /v1/admin/cv-jobs/failed?limit=20&offset=0`
```jsonc
[{ "id": "uuid", "status": "failed", "error_message": "Could not extract text.",
   "owner_email": "ada@example.com", "created_at": "2026-08-21T13:31:15Z" }]
```

### `POST /v1/admin/cv-jobs/{id}/retry`
Same response shape as above with `status` reset to `"pending"`. Re-enqueues the parse job.

### `POST /v1/admin/chatfolios/{id}/unpublish`
Same shape as the chatfolios list entry, `is_published: false`. Immediately takes the page
down — same effect as the candidate's own unpublish button, just admin-triggered. Every call
here writes an audit log entry server-side; no separate audit-log endpoint exists yet to display
that history in the UI, but the action itself is always recorded.

---

## 9. Custom domains — not live yet

`/v1/portfolio-settings/domain` (`GET`/`POST`/`DELETE`) exists in the API but returns `404` on
every call until the backend's `enable_custom_domains` feature flag is turned on — which it
isn't, anywhere, today. Don't build UI for this yet; there's nothing to point it at. If/when the
flag flips on, `POST` takes `{ "domain": "candidate.example.com" }` (validated as a real
hostname) and returns a `verification_token` — but no DNS verification flow exists on the
backend yet either, so even post-flag this would only be a "claim a domain" step, not a working
custom-domain redirect. Treat the whole feature as backend-scaffolding-only for now.

---

## 10. Rate limit summary

| Endpoint | Limit |
|---|---|
| `POST /auth/register` | 5/min per IP |
| `POST /auth/login` | 10/min per IP |
| `POST /auth/refresh`, `POST /auth/logout` | 20/min per IP |
| `POST /auth/forgot-password`, `POST /auth/reset-password` | 5/min per IP |
| `POST /auth/2fa/setup` | 5/min per IP |
| `POST /auth/2fa/verify-setup`, `POST /auth/2fa/login/verify` | 10/min per IP |
| `POST /auth/2fa/login/resend` | 3/min per IP |
| `POST /cv/upload`, `POST /cv/{id}/retry` | 10/hour per IP |
| `POST /sections/{id}/regenerate` | 10/hour per IP |
| Everything else under this doc | no explicit limit (still bounded by needing a valid token) |

All `429` responses share the same `{"detail": "..."}` shape as other errors — there's no
`Retry-After` header today, so don't build UI that depends on one; a generic "try again in a
bit" is the honest message.
