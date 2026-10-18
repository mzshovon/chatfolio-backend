# Pricing, Packages & Entitlements — Design Plan

Status: **Phases 1 (foundation), 2 (admin API), 3 (model routing) 4 (enforcement) and 5 (billing: Support Kori license activation behind a switchable `ActivationStrategy`, orders/licenses, trial, expiry/revalidation/reminder jobs, admin billing settings) implemented** — tables, seeds, `EntitlementService`, Redis cache, `GET /pricing`, `GET /me/entitlements`. **Default/fallback model is configuration, not data (changed after phase 5):** `LLM_DEFAULT_PROVIDER` + `LLM_<PROVIDER>_MODEL` + its API key/base URL select the model for intent, extraction, free users and the failure fallback; the `llm_models` table holds only the *extra* models premium users may choose (the `is_fallback` column was dropped). Phase 6 (hardening) and the native-gateway strategy are not started. Phase 5 notes: license keys are stored hashed (lookup/uniqueness) *and* Fernet-encrypted (needed to re-validate with Support Kori); `payment_events` is left for the native-gateway phase. Phase 4 decisions: `links_max` counts the primary slug plus active aliases; a recruiter whose chat is blocked only sees a neutral `CHAT_UNAVAILABLE` 403; owner notifications for exhausted quotas are not built yet. No enforcement is active yet. Per-user usage/remaining in `/me/entitlements` arrives with phase 4 (quota tracking).
Scope: Chatfolio-Be (this repo). The CMS and the public Chatfolio site consume the APIs defined in §9.

---

## 1. What exists today (verified in code)

| Area | Today | Consequence |
|---|---|---|
| Plan | `candidate_profiles.plan` (string, default `"free"`) + `usage_limits` JSON + `ai_tokens_used` counter; **nothing enforces them**. `FeatureFlags.enable_billing=False` | Replace with real plan/subscription tables; keep old columns as read-only until migrated |
| LLM | `LLMProvider` Protocol (`complete(system, messages, json_mode)`), `LLMProviderFactory.for_task(task)`; **only DeepSeek implemented**; provider chosen by env per task, never per user | Extend factory with per-user model resolution; add OpenAI + Gemini adapters |
| Chat | `ChatService.send_message` loads session → chatfolio → profile on every call; cooldown is per session | Natural single choke point for chat quota + model routing |
| Chat history | `DashboardService.list_conversations/get_conversation` return all history | Needs a time-window filter |
| Insights | `RecruiterMetadata` (name/company/role/…) returned in conversation lists + `dashboard/analytics` | Gate behind a boolean entitlement |
| Meetings | `MeetingService.schedule_public_meeting` already has a per-candidate lock + 5-min cool-down + 1-per-session rule; `schedule_meeting` (owner) has no limit | Add count quota in the same locked section |
| Publish | `PortfolioService.publish` validates sections only | Add published-link limit |
| Domains | `DomainService` is a **stub**: flag-gated, 1:1 (`PublicDomain.chatfolio_id` unique), **no DNS verification** | "N custom domains" needs schema change and real verification (see §11) |
| Redis | Already in stack (slowapi rate limit, arq queue) | No new infrastructure needed |
| Admin | `require_admin` dependency; `admin_audit_logs`; RBAC tables are non-enforcing | Admin package CRUD hangs off `require_admin` + audit log |
| Payments | None | Needs a gateway adapter (image says "Support Kori — bKash, Nagad, cards") |

---

## 2. Core design principle

> **One server-side authority: `EntitlementService`.** Every limit, feature gate and model choice is
> answered by it. The client never sends a plan, a limit, or a model it is not allowed to use; the
> JWT carries **no** entitlements. Enforcement lives in the **service layer** (not only routers), so
> background jobs and future endpoints cannot bypass it.

An *entitlement* = a (feature key → value) pair resolved for a user:
`user override` > `active subscription plan` > `free plan`.

---

## 3. Feature catalogue (what an admin can customise)

Features are keyed constants **defined in code** (because each needs an enforcement hook). Admins
control the **value per plan** and can create unlimited plans. Adding a brand-new *kind* of feature
still needs a small code change + one catalogue row; that is unavoidable and honest.

| Key | Type | Kind | Enforced at | Meaning |
|---|---|---|---|---|
| `links_max` | int | stock | alias create/update | Aliases (extra slugs) of the user's single chatfolio; all editable by the owner |
| `publishes_limit` | int, `-1` = unlimited | flow / period | `PortfolioService.publish` | Max number of **publish actions** (each publish consumes 1; unpublish is free and refunds nothing) |
| `custom_domains_max` | int | stock | `DomainService.add_domain` | Custom domains attached (no DNS verification in this phase) |
| `recruiter_chats_limit` | int, `-1` = unlimited | flow / period | `ChatService.send_message` (first msg of session) | New recruiter chat sessions per period |
| `chat_messages_per_session` | int, `-1` = unlimited | per-session | `ChatService.send_message` | Optional cap on messages in one session |
| `ai_tokens_limit` | int, `-1` = unlimited | flow / period | all LLM calls | Token budget per period (cost safety net behind "unlimited AI answers") |
| `chat_history_days` | int, `-1` = unlimited | window | `DashboardService` reads | How far back the owner can view conversations |
| `recruiter_insights` | bool | gate | `DashboardService`, analytics | Who asked / what / from where |
| `calendar_booking` | bool | gate | Google connect + both meeting paths | Google Meet booking on/off |
| `meetings_limit` | int, `-1` = unlimited | flow / period | `MeetingService` | Meetings booked per period |
| `remove_badge` | bool | gate | public portfolio response | Hide "Made with Chatfolio" badge |
| `model_selection` | bool | gate | model preference endpoint | May pick a model at all |
| `allowed_models` | many-to-many (not a value) | gate | `ModelRouter` | Which DB models this plan may use |

Kinds: **stock** = counted from real rows (always correct, no counter to drift); **flow** = consumed
over time, tracked in Redis; **window/gate** = read-time filter or boolean.

Period for flow limits: `lifetime | monthly | daily` (per feature, admin-editable). Free "n meetings"
can therefore be lifetime; premium can be monthly or unlimited.

---

## 4. Database design (Alembic migrations, all additive)

```
features                      -- code-defined catalogue, seeded by migration
  key PK, kind (stock|flow|gate|window), value_type (int|bool), period_default, description

plans
  id, slug UNIQUE, name, tagline, is_active, is_public, sort_order,
  price_minor INT, currency CHAR(3) DEFAULT 'BDT',                              -- 500 BDT
  duration_unit ('day'|'month'|'year'|'lifetime'), duration_count INT,          -- admin-editable: 1 month, 6 months, 1 year...
  external_product_ref NULL,      -- Support Kori product_id for this plan (falls back to env default)
  trial_days INT DEFAULT 0,       -- >0 => one-time free trial of this plan (see §9b)
  badge_text NULL ('MOST POPULAR'), cta_label ('Go Premium'), footnote,         -- pricing card copy
  marketing_bullets JSONB,        -- ["Up to {links_max} chatfolio links and custom domains", ...]
  is_default_free BOOL            -- exactly one; partial unique index

plan_features
  plan_id FK, feature_key FK, int_value NULL, bool_value NULL, period NULL     -- PK(plan_id, feature_key)

llm_models
  id, provider (deepseek|openai|gemini), model_id ('gpt-4o-mini', ...), display_name,
  is_active, is_fallback BOOL (exactly one: DeepSeek), supports_json_mode BOOL,
  cost_in_per_1m, cost_out_per_1m (for reporting), sort_order

plan_models                   -- which models each plan may use
  plan_id FK, model_id FK, PK(plan_id, model_id)

subscriptions
  id, user_id FK, plan_id FK, status (active|expired|cancelled|pending),
  starts_at, expires_at, source (payment|admin_grant|trial), order_id NULL
  -- at most one 'active' per user (partial unique index)

orders                        -- one row per activation, whatever the strategy
  id, user_id, plan_id, strategy ('support_kori_license' | 'native_gateway'),
  amount_minor, currency, external_ref UNIQUE-per-strategy, status (pending|paid|failed|revoked),
  raw_response JSONB (validation/webhook payload for audit)

licenses                      -- Support Kori keys; binds a key to exactly one user
  id, license_key_hash UNIQUE (SHA-256, key never stored in plaintext), key_last4,
  user_id FK, plan_id FK, order_id FK, status (active|revoked|expired),
  validated_at, last_revalidated_at, expires_at NULL

payment_events                -- native gateway only (later): webhook idempotency
  id, gateway_ref, raw_payload, signature_ok, processed_at, UNIQUE(gateway_ref, event_type)

entitlement_overrides         -- per-user admin exceptions ("give this user 10 links")
  user_id, feature_key, int_value/bool_value, expires_at NULL, reason, granted_by

user_model_preferences
  user_id PK, model_id FK NULL            -- NULL => use fallback

usage_counters                -- durable flush target for Redis flow counters (tokens, etc.)
  user_id, feature_key, period_start, used, PK(user_id, feature_key, period_start)

chatfolio_links               -- aliases of the ONE chatfolio (decided: not separate chatfolios)
  id, chatfolio_id FK, slug UNIQUE (shares the namespace with public_chatfolios.slug), is_active
  -- the primary slug stays on public_chatfolios; aliases resolve to the same page
  -- (old previous_slug redirect logic keeps working). On downgrade, extras beyond the limit
  -- are set is_active=false (newest first), never deleted.
```

Changes to existing tables: drop unique on `public_domains.chatfolio_id` (allow N). No DNS
verification this phase (decided): domains are stored and counted only; `is_verified` stays unused
and nothing may serve traffic from a domain until verification is built later. Later deprecate `candidate_profiles.plan/usage_limits`. **Free is the absence of an active subscription** (decided during phase 1): no row per free
user, so no backfill and signup stays one insert; the `is_default_free` plan is the fallback. Admin edits are written to the existing
`admin_audit_logs`.

Why not a wide `plans` table with one column per feature? Every new feature would need a migration
and the admin UI could not be generic. Key/value rows keep package customisation data-only.

---

## 5. Entitlement resolution & caching

**Resolution** (`EntitlementService.get(user_id) -> Entitlements`):
1. active subscription with `expires_at > now()` (checked at read time — we never rely on a cron to
   downgrade), else free plan;
2. overlay non-expired `entitlement_overrides`.

**Cache (Redis, same instance used today):**

| Key | Value | TTL | Invalidated by |
|---|---|---|---|
| `ent:{user_id}` | resolved entitlements JSON + `expires_at` | min(5 min, subscription expiry) | payment webhook, admin grant/override/plan edit (explicit `DEL`) |
| `plans:catalog:v{n}` | public pricing payload | 10 min | plan/feature/model edit bumps `n` |
| `models:catalog:v{n}` | active models + plan→model map | 10 min | model/plan edit bumps `n` |

Plan edits affect many users, so they **bump the catalogue version** (and `ent:*` entries embed the
version they were built against; mismatch ⇒ recompute) instead of scanning/deleting keys. Cache miss
or Redis outage ⇒ compute from Postgres (slower, still correct).

---

## 6. Quota tracking (Redis, single source for live counters)

**Stock quotas** (links, domains): *no counter.* `SELECT count(*)` inside the same transaction as
the insert, with a row lock on the user (`SELECT … FOR UPDATE` on the profile or a
`pg_advisory_xact_lock(user)` — the same pattern `MeetingRepository.lock_candidate_requests` already
uses). Two concurrent requests cannot both pass.

**Flow quotas** (chats, meetings, tokens): atomic Redis via one Lua script:

```
key = q:{user_id}:{feature}:{period_key}      -- period_key e.g. 2026-10 (UTC month) / 'lifetime'
check-and-increment: if used + amount > limit then return DENIED else INCRBY; set EXPIREAT period end (+ grace)
```

- **Reserve → commit/compensate:** reserve before the action; if the action fails (e.g. Google
  rejects the event), `DECRBY`. Mirrors the existing rule "a failed attempt must not burn the
  session's request".
- **Self-healing:** on a missing key, rebuild from the source of truth — `count(chat_sessions)` for
  chats, `count(meeting_requests)` for meetings, `usage_counters` for tokens — then continue. Redis
  loss therefore cannot grant free quota.
- **Durability:** token counters are flushed to `usage_counters` by an arq cron (every few minutes
  and on period roll). Chats/meetings already have durable rows.
- **Redis down:** fall back to the DB-derived count (slower path), never fail open.
- **Definition of "a chat":** a `ChatSession` consumes 1 on its **first message** (not on session
  creation — opening a page costs nothing). Subsequent messages are free unless
  `chat_messages_per_session` is set.
- **Period:** UTC calendar month for `monthly` (simple to explain on the pricing page). Anchoring to
  the subscription start date is possible later by changing only `period_key()`.

**When the owner is out of quota** the recruiter sees a neutral message ("This Chatfolio can't take
new chats right now") — no mention of plans/limits — and the owner gets an in-CMS notice + optional
SMS/email via the existing notification stack.

---

## 7. Model routing (DB-driven, DeepSeek as fallback)

```
ModelRouter.resolve(user_id, task) -> ResolvedModel(provider, model_id, is_fallback)
  1. entitlements.model_selection is False           -> fallback model (DeepSeek)
  2. user_model_preferences.model_id set AND
     model.is_active AND model in plan_models(plan) -> that model
  3. otherwise                                       -> fallback model
```

- **Free plan:** always fallback DeepSeek (the explicit rule). Free users may *see* premium models in
  the UI as locked, but the API rejects selecting them.
- **Premium:** may select any active model in their plan's `plan_models`.
- **Failure handling:** if the chosen provider errors/times out, retry once on the fallback and log
  it (`llm.fell_back_to_default`). The recruiter never sees a model-specific failure. If the admin
  deactivates a model in use, step 2 fails closed to fallback automatically.
- **Which tasks use the user's model?** Recommend: `CHAT` and `GENERATION` use the user's choice;
  `INTENT` (cheap classification) and `EXTRACTION`/`EMBEDDING` stay on system defaults so one
  premium user can't multiply cost on background work. (Decision point, Q4.)
- **Secrets stay out of the DB.** The DB holds model *metadata* only; API keys remain env/secret
  manager (`LLM_OPENAI_API_KEY`, `LLM_GEMINI_API_KEY` already exist in `LLMSettings`). A model whose
  provider has no key configured is treated as inactive.
- Token usage is recorded per `(user, model)` so cost per plan is reportable.

### LangChain or not? — Recommendation: **do not add LangChain.**

Our need is a single operation — `complete(system, messages, json_mode) -> text + tokens` — across
3 providers. No agents, tools, chains, or streaming. The code already has the right abstraction
(`LLMProvider` Protocol + factory) and RAG is already hand-built (Chroma + local embeddings).
LangChain would add a large, fast-moving dependency tree and an extra abstraction over something
we already abstract, and make token accounting/timeouts per task harder to keep exact.

Recommended approach, in order:
1. **Thin adapters (recommended).** One `OpenAICompatibleProvider` (DeepSeek and OpenAI share the
   `/chat/completions` shape — DeepSeekProvider is already that, just generalised with `base_url` +
   `model`), plus a `GeminiProvider`. Gemini also offers an OpenAI-compatible endpoint, which would
   let a single class cover all three; to be verified against current Google docs at build time,
   otherwise use its native `generateContent` (~40 lines). ~150 LOC total, fully typed, no new deps
   (httpx already present).
2. **LiteLLM** only if we later want 6+ providers, automatic cost tables, or router-level retries —
   a much lighter option than LangChain for pure routing.
3. **LangChain/LangGraph** only if the product grows into agentic/tool workflows (not planned).

---

## 8. Enforcement map (where each rule is hooked)

| Rule | Hook | Behaviour on violation |
|---|---|---|
| Recruiter chat limit | `ChatService.send_message` — right after the cooldown check, before any LLM call | neutral recruiter message; owner notified |
| Model choice | `RAGService` via `LLMFactory.for_task(task, user_id)` → `ModelRouter` | silent fallback to DeepSeek |
| AI token budget | `ModelRouter`/provider wrapper pre-check + post-commit of `tokens_used` | degrade to canned fallback reply; owner notified |
| Chat history window | `DashboardService.list_conversations` **and** `get_conversation` (by-id too, else ID guessing bypasses the filter) | out-of-window rows omitted / 404 |
| Recruiter insights | `DashboardService` + analytics serializers strip `recruiter_metadata` | field absent + `locked: true` hint |
| Google Meet booking | `meeting_settings` connect/disconnect/schedule + `schedule_public_meeting` | 403 `FEATURE_NOT_IN_PLAN` |
| Meeting count | inside the existing locked section of `schedule_public_meeting` + owner `schedule_meeting` | 403 `LIMIT_REACHED` |
| Publish count | `PortfolioService.publish` (atomic reserve; `unpublish` does not refund) | 403 `LIMIT_REACHED` |
| Alias links | alias create/update service (count of active `chatfolio_links` under lock) | 403 `LIMIT_REACHED` |
| Custom domains | `DomainService.add_domain` | 403 `LIMIT_REACHED` |
| Badge | public portfolio response includes `show_badge` computed from owner's entitlement | n/a |

**Anti-bypass checklist**
- Owner is always derived server-side (`chat_session → chatfolio → profile → user`); never from client input.
- Plan/model/limits never accepted from request bodies; model preference endpoint validates against `plan_models`.
- Limits enforced in services + DB constraints (partial unique indexes), not just routes/UI.
- Atomic check-and-consume (Lua / row lock) — no read-then-write races; concurrent-request tests required.
- Subscription expiry evaluated by `expires_at` at read time.
- Payment activation only via **signature-verified, idempotent** gateway webhook (never from the browser redirect).
- By-id endpoints re-check ownership **and** window (history).
- **Downgrade/expiry never deletes data:** over-limit extras (domains, links) are *deactivated* (newest first) and reactivate on re-purchase; history is hidden, not purged.
- **Layered with the "no middleware" decision:** a small `require_feature("…")` dependency on gated routes for on/off features (readability), real enforcement in the service layer, plus a **route-inventory test** that fails CI when a route touching a gated resource has neither — guards against future endpoints forgetting enforcement.
- Admin changes audited; overrides carry `reason`, `granted_by`, optional expiry.
- Existing slowapi rate limits stay as the abuse layer, separate from plan quotas.

Error contract (stable for both frontends):
```json
403 { "code": "LIMIT_REACHED" | "FEATURE_NOT_IN_PLAN", "feature": "meetings_limit",
      "limit": 3, "used": 3, "upgrade_url": "/pricing" }
```

---

## 9. API surface

**Public / shared (CMS + Chatfolio site)**
- `GET /api/v1/pricing` — no auth, cached + ETag. Returns active public plans exactly as the card in
  the screenshot needs: `name, tagline, price {amount, currency, symbol}, period_months,
  badge_text, bullets[] (rendered server-side), cta_label, footnote`. Bullets are templates such as
  `"Up to {links_max} chatfolio links and custom domains"` rendered from the plan's real values, so
  **marketing copy can never drift from what is enforced**.
- `GET /api/v1/public/{slug}` (existing) gains `show_badge`; chat-availability state.

**Candidate (CMS)**
- `GET /api/v1/me/entitlements` — plan, expiry, every feature value, **usage + remaining**, locked flags (drives UI gating; UI gating is cosmetic, server is authoritative).
- `GET /api/v1/me/models` — models allowed for me + current selection; `PUT /api/v1/me/models/preference`.
- `POST /api/v1/billing/activate {license_key}` — Support Kori strategy (§9a). Plan is derived from the validation response, never from the client.
- `POST /api/v1/billing/checkout {plan_slug}` + `POST /api/v1/billing/webhook/{gateway}` — native-gateway strategy; **built later**, route returns 404 while disabled.
- `GET /api/v1/billing/subscription` — current subscription/history.
- `GET /api/v1/billing/strategies` — which activation methods the UI should show for this user.

**Admin** (`require_admin`, all audited)
- `/admin/plans` CRUD (+ clone, activate, reorder), `/admin/plans/{id}/features` PUT (matrix of values), `/admin/plans/{id}/models` PUT
- `/admin/models` CRUD (activate/deactivate, mark fallback — exactly one)
- `/admin/features` GET (catalogue, read-only list)
- `/admin/users/{id}/subscription` grant/extend/revoke; `/admin/users/{id}/overrides` CRUD; `/admin/users/{id}/usage` view/reset
- `/admin/orders` list

### 9a. Payment activation — two switchable strategies

Support Kori handles the purchase and settlement entirely; we only receive a **license key** and get
a **validate API**. A *valid license = payment done*. Later we add our own gateway. Both sit behind
one interface so nothing else in the system (entitlements, quotas, admin) knows or cares which was used:

```
class ActivationStrategy(Protocol):
    name: str                                  # 'support_kori_license' | 'native_gateway'
    async def activate(self, user, payload) -> ActivationResult   # -> plan, period, external_ref
    async def revalidate(self, order) -> bool                     # still entitled?
```

`BillingService.activate()` is the only writer of `orders` + `subscriptions`; it calls whichever
strategy is selected and then runs identical logic: create order → create/extend subscription →
invalidate `ent:{user}` → audit log.

**Strategy 1 — `SupportKoriLicenseStrategy` (now)**
1. User pastes the key in the CMS → `POST /billing/activate`.
2. **DB check first, Kori second.** Hash the key and look it up in `licenses` *before* any network call:
   - found, **other user** → generic "invalid license" (never reveal it exists);
   - found, **same user**, still active → idempotent success; found, **expired** → rejected with
     "this license has already been used and has expired — please purchase a new one". Kori keeps
     answering `valid:true` for an old key forever, so **our DB is the authority on "already used" and
     "expired"; Kori is only the authority on "was this key ever genuinely sold"**;
   - not found → continue.
3. Call `POST https://supportkori.com/api/licenses/verify` with `{product_id, license_key}`
   (`product_id` = `plans.external_product_ref` of the requested plan, defaulting to env
   `SUPPORT_KORI_PRODUCT_ID`; server-side only, short timeout, one retry). Response used:
   `valid`, `product`, `purchased_at`, `uses`, `seats`. It returns **no expiry and no plan id**, so:
   - accept only `valid == true`; store `purchased_at` + raw response for audit;
   - **ignore `uses`/`seats`** for access decisions (we don't know whether `verify` increments `uses`,
     and we enforce 1 key = 1 user ourselves);
   - the plan is the one whose `product_id` Kori accepted the key for. Letting the client name the plan
     is safe *only if* Kori rejects a key under a mismatched `product_id` — **to be verified with a
     test key before relying on it** (if it doesn't, we need one product per plan and a server-side
     lookup instead).
4. Valid → **claim the key by INSERT into `licenses` with `UNIQUE(license_key_hash)`** in the same
   transaction as the order/subscription. Two simultaneous submissions: one wins, the other hits the
   unique violation and gets "already used". Same user never gets two subscriptions from one key.
5. **Expiry is computed by us:** `expires_at = activation_time + plan duration` (unit/count from the
   plan row; calendar-month arithmetic). If the user still has an active subscription of that plan,
   the new key **extends from the current `expires_at`**. Admin-optional rule `max_activation_delay_days`
   (default off): reject keys whose `purchased_at` is older than N days, to stop hoarded keys.
6. Abuse control: strict slowapi limit + per-user attempt counter on `/billing/activate` (brute-forcing
   keys), keys never logged (last 4 only), raw validation response kept in `orders.raw_response`.
7. Optional re-validation (arq cron, daily/weekly) to catch refunds/revoked keys: revoked ⇒
   subscription ends, user falls back to free. Kori API outage ⇒ **keep current access** (never
   downgrade on a network error, only on an explicit "invalid/revoked").

Re-validation note: the verify response has no "revoked" field, so a later `valid:false` is the only
refund/revoke signal we get. That ends the subscription; a network error or non-200 never does.

**Strategy 2 — `NativeGatewayStrategy` (later)**
Checkout → redirect → signature-verified idempotent webhook → same `BillingService.activate()`.
Only the strategy class and the two routes are new; schema (`orders`, `payment_events`) is already
in place so there is no later migration of the entitlement system.

**Switching.** Admin-editable setting `billing.enabled_strategies` (DB row, cached) — plus a
`plans.allowed_strategies` column so a given plan can be sold through Kori only, native only, or
both, and `GET /billing/strategies` returns what the UI offers per user. Because `orders.strategy`
is stored per purchase, old Kori licenses keep working/re-validating after native checkout is
introduced; the two coexist. Manual admin grant remains a third `source` for support/testing.

### 9b. Package duration & free tier

- **Duration is admin data**, not code: `duration_unit` + `duration_count` per plan covers monthly
  (1 month), half-yearly (6), yearly (1 year), any custom N days/months, or `lifetime`. Changing a plan's
  duration affects only **future** activations; running subscriptions keep the `expires_at` they were
  granted. The pricing card's "/ 6 months" label is rendered from these fields.
- **Several paid plans through Support Kori** (e.g. monthly + yearly) need one Kori product each;
  put each product's id in `plans.external_product_ref`.
- **Free tier** stays permanent and visible as its own card on the pricing page (your screenshot already
  shows a card to its left) — it is the funnel. To grab more attention, I recommend an admin-toggleable
  **one-time Premium trial**: `plans.trial_days` (e.g. 7), auto-granted as a `source='trial'`
  subscription (no license key). One trial per user via a partial unique index; abuse mitigation by
  requiring a verified phone (2FA already exists) before the trial starts. Default **off** until you
  decide; flipping it on is a data change. Trial expiry flows through the same expiry/hide-not-delete
  rules, with a reminder 1 day before the end (existing SMS/email).

**New code layout** (follows existing conventions: models / schemas / repositories / services / api):
`models/billing.py, models/llm_model.py`, `services/entitlement_service.py, quota_service.py,
model_router.py, billing_service.py, plan_admin_service.py`, `llm/providers/openai_compatible.py,
gemini.py`, `billing/strategy.py` (Protocol) + `billing/support_kori.py` (+ `billing/native_gateway.py` later),
`api/v1/pricing.py, billing.py, me.py, admin_plans.py`, arq jobs: `expire_subscriptions`,
`flush_usage_counters`, `expiry_reminder`, `revalidate_licenses`.

---

## 10. Proposed seed values (all editable by admin; placeholders to confirm)

| Feature | Free | Premium (৳500 / 6 mo) |
|---|---|---|
| `links_max` (aliases) | 1 | 5 |
| `publishes_limit` | 3 lifetime | unlimited |
| `custom_domains_max` | 0 | 5 |
| `recruiter_chats_limit` | 20 / month | unlimited |
| `ai_tokens_limit` | 200k / month | 5M / month (fair-use cap) |
| `chat_history_days` | 7 | unlimited |
| `recruiter_insights` | off | on |
| `calendar_booking` | on, `meetings_limit` = 2 lifetime | on, unlimited |
| `remove_badge` | off | on |
| `model_selection` | off (DeepSeek) | on: DeepSeek + GPT + Gemini |

Premium card copy is taken directly from the screenshot (bullets, badge, CTA, footnote) and is stored
as `plans` data, not hard-coded in either frontend.

---

## 11. Decisions & remaining questions

**Decided**
1. Links = **5 aliases of one chatfolio**, owner-editable (new `chatfolio_links` table). Not separate chatfolios.
2. Custom domains: **no DNS verification** this phase; stored and counted only.
3. Payments: **Support Kori license-key validation = payment done**, behind a switchable strategy interface; own gateway added later (§9a).
4. Publishing limit = **max N publish actions** (`publishes_limit`), not simultaneous links.
5. Chat history older than the plan window is **hidden, not purged** (restores on upgrade).
6. `INTENT` classification **stays on DeepSeek**; user-selected model applies to `CHAT` and `GENERATION` only.

7. Support Kori spec received: `POST /api/licenses/verify {product_id, license_key}` → `{valid, product, purchased_at, uses, seats}`. No expiry/plan id returned, so expiry is ours; one-time use and expiry are enforced from our `licenses` table, checked **before** calling Kori (§9a).
8. Plan duration is admin-customisable (unit + count, incl. lifetime); free tier stays, optional one-time trial (§9b).

**Still needed / defaults I will assume unless you say otherwise**
- **Test with a real key** that (a) Kori rejects a key sent with a wrong `product_id`, (b) whether `verify` increments `uses`, (c) what an invalid response looks like (HTTP status + body).
- Quota period: UTC calendar month (default).
- Renewal: a new valid key **extends** from the current expiry (stacks); the same expired key can never be reused.
- Trial: off by default.
- Free `publishes_limit` = 3 lifetime and free `meetings_limit` = 2 lifetime (placeholders, §10).
- Refunds/coupons/trials: out of scope; a revoked license (if Kori reports it) ends the subscription.
- Risk accepted by skipping DNS verification: a user could attach a domain they do not own. It is inert until a later phase makes domains serve traffic — do not enable traffic routing before verification exists.

---

## 12. Phased delivery

1. **Foundation** — tables, seeds, `EntitlementService`, Redis cache, `/me/entitlements`, `/pricing`, backfill free subscriptions. *No behaviour change yet.*
2. **Admin** — plans/features/models CRUD, manual grants, audit log.
3. **Model routing** — OpenAI-compatible + Gemini providers, `ModelRouter`, preference API, fallback, per-model token accounting.
4. **Enforcement** — chat quota, history window, insights gate, meeting gate/count, publish/links/domains (each behind a flag so they can be switched on one at a time).
5. **Billing** — `ActivationStrategy` interface, Support Kori license strategy (mock until the API spec arrives), `licenses`/`orders`, activate endpoint, expiry + re-validation jobs, reminders. The native gateway strategy is a later, separate phase.
6. **Hardening** — concurrency tests (parallel chat/meeting/publish at limit), Redis-flush recovery test, webhook replay test, load test via existing `scripts/load_test_chat.py`, metrics (`quota_denied_total`, `model_fallback_total`, cost per plan).

Testing follows the existing pytest integration style; the bypass tests are explicit acceptance
criteria: free user selecting a premium model → rejected; forged `session_id` of another candidate's
chat; replayed webhook; expired subscription mid-cache; 50 parallel first-messages at quota-1.

Estimated order of work is 1 → 2 → 3 → 4 → 5; phases 1–4 are deliverable and demoable without the payment gateway.
