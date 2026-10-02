# Public Chat UI — Frontend Reference

Backend reference for the **public-facing** frontend: a candidate's published Chatfolio page
(profile info + optional CV download) and the recruiter chat widget embedded on it. Nothing in
this document requires authentication — every endpoint here is meant to be called directly from
a browser with no login step.

Companion doc: [`ADMIN_PANEL_UI_REFERENCE.md`](./ADMIN_PANEL_UI_REFERENCE.md) covers the
authenticated candidate/admin app. Full backend design: [`BACKEND_PLAN.md`](./BACKEND_PLAN.md).

---

## 1. Base URL & conventions

- All endpoints are prefixed `/api/v1`. Example base: `https://api.chatfolio.example.com/api/v1`.
- JSON in, JSON out. `Content-Type: application/json` on every request with a body.
- No authentication on any endpoint in this document — don't send an `Authorization` header,
  there's nothing to send.
- Errors always come back as `{"detail": "human-readable message"}` with a non-2xx status code.
  There is no machine-readable error `code` field today — branch on HTTP status, not on the
  message text (message text may change).

---

## 2. Portfolio page

### `GET /api/v1/public/chatfolio/{slug}`

Everything needed to render a candidate's public page in one call.

**Responses**

| Status | Meaning |
|---|---|
| `200` | Page data below |
| `307` | The slug was renamed — `Location` header points at `/api/v1/public/chatfolio/{new-slug}`. Follow it (browsers and `fetch` do this automatically; if you're calling from a server-side proxy, follow redirects explicitly). |
| `404` | Slug doesn't exist, or the Chatfolio isn't published. Show a generic "not found" page — don't distinguish the two cases (a candidate may have unpublished intentionally). |

```jsonc
// 200 OK
{
  "slug": "ada-lovelace",
  "full_name": "Ada Lovelace",
  "title": "Backend Engineer",
  "location": "London, UK",
  "job_type": "remote",                      // nullable — "remote" | "onsite" | "hybrid"
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

### `GET /api/v1/public/chatfolio/search`

Recruiter-facing discovery: find candidates without already knowing their slug. All query
params are optional and combine with AND — call with none of them for the 25 most recently
published Chatfolios.

| Param | Match type | Example |
|---|---|---|
| `username` | Case-insensitive partial match on the slug | `?username=ada` |
| `location` | Case-insensitive partial match on the candidate's location | `?location=london` |
| `job_type` | Exact match, one of `remote` \| `onsite` \| `hybrid` | `?job_type=remote` |
| `field` | Case-insensitive partial match on the candidate's title, bio, **or the role of any experience**, e.g. "Software Engineer" (a candidate with no title set is still found via their experience roles) | `?field=engineer` |

```jsonc
// GET /api/v1/public/chatfolio/search?location=london&job_type=remote
// 200 OK — plain array, deliberately lightweight (not the full portfolio payload above)
[
  { "slug": "ada-lovelace", "full_name": "Ada Lovelace", "recruiter_count": 6 }
]
```

**Notes for the UI:**
- This is a *results list*, not a profile page — use each `slug` to link into
  `GET /api/v1/public/chatfolio/{slug}` (above) for the full profile once a recruiter picks a
  result.
- Only published Chatfolios are ever returned, same guarantee as the single-slug endpoint.
- Results are ordered most-recently-published first, capped at 25 — there's no `limit`/`offset`
  yet, so don't build pagination controls against this endpoint until that's added.
- An empty array is a normal "no matches" response, not an error — don't treat `[]` as a `404`.

### `GET /api/v1/public/chatfolio/{slug}/cv`

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

### `POST /api/v1/public/chat/{slug}/sessions`

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

### `POST /api/v1/public/chat/sessions/{session_id}/messages`

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
`availability_inquiry`, `contact_request`, `meeting_request`, `general_introduction`, or `unknown` (off-topic
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

**`meeting_request`** is returned when the recruiter wants a meeting, call, interview or
discussion ("can we schedule a call?", "let's discuss the role", "ekta meeting korte chai"). The
reply is a normal text answer that points them to the meeting option in the chat, and never
confirms a time or invents a link. **Use this intent to open the "Request a meeting" form**
(section 6) next to or under that reply — and if you previously got a `409`/`403` from the meetings
endpoint, show the contact CTA instead. It does not share the candidate's email/phone (that is
still `contact_request` only); a message asking for both is classified `meeting_request`.

**`contact_request` and `availability_inquiry` accuracy:** every reply is grounded in the
candidate's real `contact_email`/`phone`/`location`/`job_type` straight from their profile
(never fabricated or inferred) — a "how do I reach you?" question gets back the exact
`contact_email`/`phone` from the portfolio payload above, or an honest "not provided" if the
candidate hasn't set one, never a guessed address. Nothing about the response shape changed;
this only affects answer accuracy for contact/location/work-mode questions.

**Contact info is only shared on contact intent, not proactively.** The backend only includes
the candidate's email/phone in the model's context at all when the message is classified as a
contact request — asking for the number/email directly, asking how to contact/call/message the
candidate, saying they (or their HR) will send an interview invite or follow up, in English,
Bangla, or Banglish. For every other message (including "are you open to new roles?", "tell me
about your experience," general interest/fit questions), the model never even sees the contact
details, so it structurally cannot leak them. Don't build UI that assumes every reply might
contain contact info; only look for it in response to an actual contact-style question.

**Answers are meant to stay scoped to the exact question asked, even across turns.** Conversation
history (the last 5 messages) is used for continuity — pronouns, follow-ups, tone — and is
prompted to never be treated as a source of what to answer with. If a recruiter asks two
related-but-distinct follow-up questions (e.g. "what cultural challenges have you faced?" then
"what technical challenges have you faced?"), each reply is instructed to cover only the category
just asked, not fold in the previous turn's answer "for completeness." Unlike the contact-sharing
guarantee above (which is structurally enforced — the data simply isn't in context unless
warranted), this is prompt-level guidance to the generation model, not a hard backend guarantee —
if a source passage blends multiple categories in one sentence, the model can still occasionally
echo both. Report recurring cases; the fix there is finer-grained retrieval chunking, a larger
follow-up.

**Persisting `session_id` client-side:** store it in memory (a React/Vue state variable, a
closure) for the page's lifetime. `sessionStorage` is fine too if the widget needs to survive a
page reload within the same tab — there's nothing sensitive in a session id (it's an opaque
random UUID, not a credential), so no XSS-storage concern like there is for the auth tokens in
the companion doc. Don't persist it to `localStorage` across browser sessions; starting a fresh
session on a new visit is the intended behavior.

---

## 4. Feedback

### `POST /api/v1/public/feedback`

Open, general product feedback — no auth required, and not tied to any candidate's slug or a
chat session. Use this for a standalone "rate your experience" widget, not as part of the chat
flow above.

```jsonc
// Request
{ "nps_score": 4, "message": "Loved the chat experience!" }
// nps_score: integer 0-5, required. message: optional, ≤2100 chars.

// 201 Created
{
  "id": "9856d9d6-65dd-4102-8b3d-99bb272c6502",
  "nps_score": 4,
  "message": "Loved the chat experience!",
  "created_at": "2026-09-11T10:00:00Z"
}
```

`422` if `nps_score` is missing or outside `0`-`5`, or if `message` is longer than 2100
characters. `message` can be omitted entirely (or sent as `null`) — only `nps_score` is
mandatory.

**Rate limit: 10 submissions per minute per IP**, same handling as the chat session-start limit
above — on `429`, show a generic "please try again in a moment."

There's no way to read feedback back from this document's endpoints — submissions are
admin-only to view (`GET /admin/feedback`, see the companion
[`ADMIN_PANEL_UI_REFERENCE.md`](./ADMIN_PANEL_UI_REFERENCE.md)).

---

## 5. Suggested widget flow

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

on "Request a meeting" submit (section 6):
  POST /public/chat/sessions/{session_id}/meetings with a fresh request_id
  on 201: show meet_link + time; on 409: hide the action, fall back to the contact CTA
```

No polling, no websockets — this is a plain request/response API. If a future phase adds
streaming, it'll be a separate documented endpoint; don't build around an assumption of one.

---

## 6. Request a Google Meet from the chat

### `POST /api/v1/public/chat/sessions/{session_id}/meetings` — no auth, 201

Lets a recruiter schedule a Google Meet with the candidate from the chat widget. The candidate
is resolved from the chat session (the same `session_id` as the messages endpoint), never from
the body — **no `Authorization` header is needed or read**. The backend loads the candidate's
stored Google token from the DB and, if it is expired or near expiry, refreshes it with Google's
refresh token automatically; the recruiter never sees any token. If the refresh is rejected (the
candidate revoked access), the call returns the generic `409` below. The meeting is created on the **candidate's** Google Calendar and Google emails the
invite to the recruiter's address.

```jsonc
// request
{
  "user_id": "uuid",                            // optional — candidate's user id; if sent it must match the session's candidate, else 404
  "attendee_email": "recruiter@company.com",   // required — where the invite goes
  "attendee_name": "Sam Rivera",               // optional, shown in the event title
  "additional_attendees": "hr@company.com, cto@company.com",  // optional — comma-separated extra invitees, max 10
  "start": "2026-09-19T17:00:00+06:00",        // required — must include a UTC offset ("Z" is fine), in the future
  "duration_minutes": 30,                       // optional, 15-120, default 30
  "timezone": "Asia/Dhaka",                     // optional IANA name, default "UTC"; use Intl.DateTimeFormat().resolvedOptions().timeZone
  "message": "Would love to discuss the backend role.",  // optional, max 500 chars, added to the event description
  "request_id": "6b1c2f0e-..."                  // optional idempotency key, 8-100 chars
}

// 201 Created
{
  "meet_link": "https://meet.google.com/abc-defg-hij",   // null in the rare case Google is still provisioning it
  "title": "Interview via Chatfolio with Sam Rivera",
  "start": "2026-09-19T17:00:00+06:00",
  "end": "2026-09-19T17:30:00+06:00",
  "timezone": "Asia/Dhaka",
  "attendee_emails": ["recruiter@company.com", "hr@company.com", "cto@company.com"]  // everyone Google emailed
}
```

**Notes for the UI:**
- `additional_attendees` is a plain comma-separated string (a single text input is fine). Spaces
  are trimmed, case is ignored, and blanks/duplicates (including `attendee_email` itself) are
  dropped. Any invalid address → `422` for the whole request; more than 10 → `422`. Everyone
  listed gets the Google invite, and `attendee_emails` in the response is the final list.
- Build `start` from a date/time picker plus the browser's timezone offset
  (e.g. `new Date(...).toISOString()` gives a valid `Z` value). Naive strings like
  `2026-09-19T17:00:00` are rejected with `422`.
- Generate one `request_id` (`crypto.randomUUID()`) per booking attempt and **reuse it on retries**
  so a double click or timeout retry doesn't create a second Meet. Also disable the submit button
  while the call is in flight.
- Render the confirmation as an assistant-style chat bubble with the `meet_link` and the time in
  the recruiter's timezone, and tell them an invite was emailed to `attendee_email`.
- The candidate's calendar link, event id and email are deliberately not returned.

| Status | Meaning | Suggested UI |
|---|---|---|
| 201 | Meet created, invite emailed | Show `meet_link` + time |
| 403 | **A meeting was already requested from this chat session** (one successful request per session, ever) | Replace the form with "Meeting requested - check your email for the invite"; don't offer a retry |
| 404 | Unknown chat session, Chatfolio no longer published, or `user_id` doesn't match the session's candidate | Same as chat's 404: offer to restart the chat |
| 409 | The candidate isn't accepting meeting requests (calendar not connected or access revoked) — deliberately one generic message | Hide/disable the "Request a meeting" action and point to the contact CTA (`contact_email`) |
| 422 | Bad email, `start` missing an offset or in the past, bad `timezone`, `duration_minutes` out of range, or Google rejected the request | Inline field errors / "pick a later time" |
| 429 | Either the IP rate limit (**3 meeting requests per hour**) or the **candidate cool-down: another session got a meeting with this candidate in the last 5 minutes** (each call emails an invite from the candidate's account) | "This candidate was just booked, please try again in a few minutes"; keep the form, allow a later retry |
| 503 | Google Calendar unavailable or not configured | "Scheduling is temporarily unavailable" + contact CTA |

**Duplicate protection:** a request only counts once Google has created the event, so a failed
attempt (422/503/409) never uses up the session's one request or starts the cool-down. After a
`201`, any further request from the same session is a `403`; requests from *other* sessions to
the same candidate are `429` for 5 minutes after the last successful one. Reusing a `request_id`
does not bypass either rule. Store "meeting requested" in widget state after a `201` and hide the
action, rather than relying on the `403`.

**Discovering availability:** the public payloads don't say whether a candidate has a calendar
connected, so the first `409` is how the widget finds out. Show the meeting action by default,
and hide it for the rest of the session on a `409`. There's still no free-slots/availability
endpoint, so the picker can't hide busy times; the candidate sees conflicts on their own calendar.

**Chat itself doesn't schedule.** The assistant's replies don't read the calendar or create events;
"can we meet Tuesday?" gets a normal grounded answer. Booking only happens through this endpoint,
typically from a "Request a meeting" button next to the input. Candidate-side connection setup is
in section 8 of [`Required_API_Doc.md`](./Required_API_Doc.md); never show Google tokens, the
connected account email or scopes in any recruiter view.
