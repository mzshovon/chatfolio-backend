# Public Chat UI — Frontend Reference

Backend reference for the **public-facing** frontend: a candidate's published Chatfolio page
(profile info + optional CV download) and the recruiter chat widget embedded on it. Nothing in
this document requires authentication — every endpoint here is meant to be called directly from
a browser with no login step.

Companion doc: [`ADMIN_PANEL_UI_REFERENCE.md`](./ADMIN_PANEL_UI_REFERENCE.md) covers the
authenticated candidate/admin app. Full backend design: [`BACKEND_PLAN.md`](./BACKEND_PLAN.md).

---

## 1. Base URL & conventions

- All endpoints are prefixed `/v1`. Example base: `https://api.chatfolio.example.com/v1`.
- JSON in, JSON out. `Content-Type: application/json` on every request with a body.
- No authentication on any endpoint in this document — don't send an `Authorization` header,
  there's nothing to send.
- Errors always come back as `{"detail": "human-readable message"}` with a non-2xx status code.
  There is no machine-readable error `code` field today — branch on HTTP status, not on the
  message text (message text may change).

---

## 2. Portfolio page

### `GET /v1/public/chatfolio/{slug}`

Everything needed to render a candidate's public page in one call.

**Responses**

| Status | Meaning |
|---|---|
| `200` | Page data below |
| `307` | The slug was renamed — `Location` header points at `/v1/public/chatfolio/{new-slug}`. Follow it (browsers and `fetch` do this automatically; if you're calling from a server-side proxy, follow redirects explicitly). |
| `404` | Slug doesn't exist, or the Chatfolio isn't published. Show a generic "not found" page — don't distinguish the two cases (a candidate may have unpublished intentionally). |

```jsonc
// 200 OK
{
  "slug": "ada-lovelace",
  "full_name": "Ada Lovelace",
  "title": "Backend Engineer",
  "location": "London, UK",
  "contact_email": "ada@example.com",       // nullable
  "phone": null,                             // nullable
  "social_links": { "github": "https://github.com/ada", "linkedin": "..." },
  "intro": "I'm a backend engineer who...",  // nullable — approved AI-generated intro section
  "summary": "Over the past 5 years...",     // nullable — approved AI-generated summary section
  "experiences": [
    {
      "id": "uuid",
      "company": "Acme",
      "role": "Senior Engineer",
      "start_date": "2022-08-23",            // date, nullable
      "end_date": null,                      // nullable — null + is_current=true means "present"
      "is_current": true,
      "description": "Led the payments platform rewrite."
    }
  ],
  "projects": [
    {
      "id": "uuid",
      "title": "CFL — Closed Feedback Loop",
      "description": "Automation of survey collection.",
      "tech_stack": ["Laravel", "PHP", "MySQL"],
      "impact": "Reduced manual review time by 40%.",
      "links": { "repo": "https://github.com/..." }
    }
  ],
  "skills": [
    { "id": "uuid", "name": "PHP", "category": "Language", "proficiency": "Excellent" }
  ],
  "education": [
    {
      "id": "uuid",
      "institution": "MIT",
      "degree": "BSc",
      "field": "Computer Science",
      "start_date": "2016-09-01",
      "end_date": "2020-06-01"
    }
  ],
  "contact_cta_config": { "label": "Get in touch", "url": "mailto:ada@example.com" },
  "cv_downloadable": true,
  "recruiter_count": 6
}
```

**Notes for the UI:**
- `intro`/`summary` are `null` until the candidate has approved those sections — render the page
  gracefully without them (they're supplementary copy, not required fields).
- `experiences`/`projects`/`education` arrays can be empty — never assume at least one entry.
- `recruiter_count` is how many distinct chat sessions had a recruiter volunteer their name or
  company at some point in the conversation — not a raw visit/session count. A recruiter who
  chats without ever mentioning who they are doesn't count, since `RecruiterMetadata` capture is
  best-effort and most sessions never populate it. Useful for a "N recruiters have reached out"
  social-proof stat, but don't present it as total page views or total chat sessions — it's a
  meaningfully smaller, more specific number than either.
- Only **approved** content is ever returned here; there's no way to accidentally see a
  candidate's draft/unpublished edits through this endpoint.

### `GET /v1/public/chatfolio/{slug}/cv`

Redirects (`307`, or `404` if unavailable) to a **short-lived presigned download URL** (1 hour
TTL) for the candidate's most recently parsed CV. Point an `<a href>` or `window.location`
directly at this URL — don't fetch it via `fetch()`/XHR and re-serve the bytes yourself, and
don't cache the redirect target (it expires and a fresh call issues a new one).

Returns `404` if `cv_downloadable` is `false` on the portfolio payload above, or if the candidate
has no successfully parsed CV — check `cv_downloadable` before showing a download button at all.

---

## 3. Chat widget

Three-step flow: start a session once per page load, then send messages against that session id
for the rest of the visit. There is no "end session" call — sessions just stop being used.

### `POST /v1/public/chat/{slug}/sessions`

Call this once when the chat widget first opens (not on every message).

```jsonc
// Request: no body
// 200 OK
{ "session_id": "9856d9d6-65dd-4102-8b3d-99bb272c6502" }
```

`404` if the slug doesn't exist or isn't published — same handling as the portfolio page.

**Rate limit: 10 session-starts per minute per IP.** A recruiter opening the widget once per
page load will never hit this; it exists to stop a script from mass-creating sessions. On `429`,
show a generic "please try again in a moment" — don't retry automatically in a loop.

### `POST /v1/public/chat/sessions/{session_id}/messages`

```jsonc
// Request
{ "content": "What is your experience with backend development?" }  // 1-2000 chars

// 200 OK
{
  "role": "assistant",
  "content": "I work primarily with PHP and TypeScript...",
  "intent": "skill_inquiry",   // the classified intent of the recruiter's message this reply answers
  "created_at": "2026-08-21T13:04:04.589723Z"
}
```

**`intent`** is always populated (never `null`) — classification runs on every message before anything
else happens, including on the fallback path. Use it to key widget behavior (e.g. show a "skills"
card, a "contact" CTA) off the turn that was just answered. One of: `skill_inquiry`,
`project_inquiry`, `experience_inquiry`, `education_inquiry`, `role_fit_inquiry`,
`availability_inquiry`, `contact_request`, `general_introduction`, or `unknown` (off-topic
messages, or a rare classifier failure — treat it as "no specific intent detected," not an error).

**Error responses to handle explicitly:**

| Status | Cause | Suggested UI behavior |
|---|---|---|
| `404` | Unknown `session_id`, or the Chatfolio was unpublished mid-conversation | Show "this chat is no longer available"; don't offer retry, start a fresh session instead |
| `422` | `content` empty or over 2000 chars | Client-side validation should prevent this; if it happens, show a field-level error |
| `429` (session cooldown) | Same session sent another message within 2 seconds of the last one | Disable the send button for ~2s after each send — this is the expected UX, not an edge case to special-case in error handling |
| `429` (rate limit) | More than 15 messages/min from this IP across all sessions | Show "you're sending messages too quickly, please slow down" |
| `503` | The LLM call itself failed (upstream provider issue) | Show "chat is temporarily unavailable, try again shortly" — this is retryable, unlike a `404` |

**On the content itself:** every reply is grounded in the candidate's approved profile data, or
is the fixed fallback sentence *"I do not have that information in my profile yet, but you can
contact me directly for details."* There's no streaming — replies come back as one complete JSON
response per call, so a simple "sending..." indicator (not a token-by-token typing effect) is
the honest UX. A real LLM round-trip currently averages ~4s p50/p95 (measured under load,
`scripts/load_test_chat.py`) — design the sending indicator for that timescale, not sub-second.

**Persisting `session_id` client-side:** store it in memory (a React/Vue state variable, a
closure) for the page's lifetime. `sessionStorage` is fine too if the widget needs to survive a
page reload within the same tab — there's nothing sensitive in a session id (it's an opaque
random UUID, not a credential), so no XSS-storage concern like there is for the auth tokens in
the companion doc. Don't persist it to `localStorage` across browser sessions; starting a fresh
session on a new visit is the intended behavior.

---

## 4. Suggested widget flow

```
on widget mount:
  POST /public/chat/{slug}/sessions  →  store session_id in component state

on user sends message:
  optimistically render the user's own message
  disable input
  POST /public/chat/sessions/{session_id}/messages
  on 200: render assistant reply, re-enable input after ~2s (cooldown window)
  on 429 (cooldown): just re-enable input once the 2s has elapsed, no error toast needed
  on 429 (rate limit) / 503: show an inline error, re-enable input immediately
  on 404: show "chat unavailable", offer to restart (re-run the session-start step)
```

No polling, no websockets — this is a plain request/response API. If a future phase adds
streaming, it'll be a separate documented endpoint; don't build around an assumption of one.
