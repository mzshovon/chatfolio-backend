# Chatfolio Backend — Engineering Knowledge Base

> Living document. Update this file whenever an architectural decision, module, or scope changes.
> Do not let code drift from this doc — if you build something differently than described here, edit this file in the same PR.
> See [Changelog](#16-changelog) at the bottom for update rules.

Source of truth for functional scope: `Requirement.md` (repo root). This document is the *how*; `Requirement.md` is the *what*.

Building a frontend against this API? Two companion references, each scoped to one frontend app: [`PUBLIC_CHAT_UI_REFERENCE.md`](./PUBLIC_CHAT_UI_REFERENCE.md) (unauthenticated recruiter-facing chat + portfolio page) and [`ADMIN_PANEL_UI_REFERENCE.md`](./ADMIN_PANEL_UI_REFERENCE.md) (authenticated candidate dashboard + admin ops, one app with role-based views). This doc stays the backend's own design record; those two are consumer-facing API docs and should be kept in sync whenever an endpoint's request/response shape changes here.

---

## 1. Tech Stack & Rationale

| Concern | Choice | Why |
|---|---|---|
| Language/runtime | Python 3.12 | Async-native, typing improvements |
| Web framework | FastAPI | Async, Pydantic-native validation, OpenAPI for free |
| Package/dep manager | `uv` | Fast, lockfile-based, replaces pip/poetry friction |
| Validation / schemas | Pydantic v2 | Already FastAPI's native layer, fast (Rust core) |
| Settings | `pydantic-settings` | Typed, env-driven config (see §4) |
| ORM | SQLAlchemy 2.0 (async, `asyncpg`) | Explicit, mature, works well with repository pattern |
| Database | PostgreSQL 16 | Relational integrity for ownership/relations; JSONB for flexible fields (social links, tech stack) |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| Cache / rate limit / queue broker | Redis | One infra dependency serving three purposes (KISS) |
| Background jobs | `arq` | Async-native, Redis-based, far lighter than Celery — fits our job list (parse CV, embed, regenerate) |
| Vector DB | Chroma | Per requirement decision. Wrapped behind an adapter (see §7) so it is swappable |
| Object storage | S3-compatible (MinIO locally, AWS S3 in prod) via `aioboto3` | CV files need durable, presignable storage |
| Auth | JWT (access + refresh), `passlib[argon2]` for hashing, `PyJWT` | Stateless auth suits public-heavy read APIs; argon2 over bcrypt for future-proof hashing |
| CV text extraction | `pymupdf` (PDF), `python-docx` (DOCX/DOC) | Reliable, no external service dependency |
| LLM access | `httpx` async clients per provider, unified interface | See §7 — multi-provider is a hard requirement |
| Rate limiting | `slowapi` (Redis backend) | Simple, works natively with FastAPI dependencies |
| Logging | `structlog`, JSON output | Structured logs needed for admin usage metrics later |
| Error tracking | Sentry SDK | Cheap to add, high value for pilot debugging |
| Metrics | `prometheus-fastapi-instrumentator` | Optional but zero-effort to wire in early |
| Testing | `pytest`, `pytest-asyncio`, `httpx.AsyncClient`, `polyfactory` | Async-first stack, factories over fixtures duplication |
| Lint/format/types | `ruff`, `mypy` | Single fast tool for lint+format, strict typing at boundaries |
| Containerization | Docker + Docker Compose | Local parity: api, worker, postgres, redis, chroma, minio |
| CI | GitHub Actions | lint → typecheck → test → build |

**Not chosen (and why):** Celery (too heavy for our job volume/complexity vs `arq`), MongoDB (relational ownership model fits Postgres better; JSONB covers flexible fields), GraphQL (no requirement calls for it — YAGNI), microservices split (single deployable service is right size for pilot — YAGNI until scale demands it).

---

## 2. Architecture & Design Patterns

Layered / clean architecture, one direction of dependency:

```
Router (FastAPI endpoint)
  -> Service (business logic, orchestration, guardrails)
    -> Repository (data access, one per aggregate)
      -> SQLAlchemy model / Vector adapter / Storage adapter / LLM provider
```

Routers never touch the DB session or ORM models directly. Services never import FastAPI (`Request`, `Depends`) so they stay testable in isolation.

### Patterns in use, and why each earns its place

- **Repository pattern** — one repository per aggregate (`UserRepository`, `ProfileRepository`, `ChatRepository`, ...). Keeps SQLAlchemy query code out of services; makes services unit-testable with fake repositories.
- **Unit of Work** — a request-scoped `AsyncSession` (via `Depends(get_db_session)`) is the transaction boundary. Services receive the session through repositories; commit/rollback happens once per request at the router/dependency edge, not scattered across services.
- **DTO separation** — Pydantic schemas (`schemas/`) are never the same classes as SQLAlchemy models (`models/`). Mapping is explicit (`from_orm`/model methods), so the DB schema can evolve without breaking the public API contract, and public API responses never accidentally leak a column.
- **Strategy pattern** — `LLMProvider` is an interface; `DeepSeekProvider`, `OpenAIProvider`, `GeminiProvider`, `ClaudeProvider`, `GrokProvider`, `OpenRouterProvider` are interchangeable implementations. Same shape for `VectorStore` (Chroma today, swappable later) and `StorageBackend` (S3/MinIO).
- **Factory pattern** — `LLMProviderFactory.for_task(task: LLMTask) -> LLMProvider` resolves the configured provider+model per task (extraction vs. chat vs. intent classification) from config. This is what makes "deepseek by default, but gpt/gemini/claude/grok/openrouter configurable" a config change, not a code change.
- **Policy/guard dependencies** — ownership and role checks (`require_owner(profile_id)`, `require_admin`) live as FastAPI dependencies, not inline `if` checks scattered through routers. One place to audit "who can touch what."
- **Adapter pattern** — `VectorStore` and `StorageBackend` interfaces isolate Chroma and S3/MinIO specifics from services, so a provider swap (e.g., Chroma → pgvector, MinIO → S3) touches one adapter file only.
- **Idempotent job triggers** — re-uploading a CV or re-requesting embedding generation is safe to call twice; jobs key off `(entity_id, content_hash)` so a retry doesn't double-process.

### Explicitly avoided (YAGNI)

- No event bus / message broker abstraction beyond the `arq` job queue we already need. "Profile approved → refresh embeddings" is a direct service call today; if a second consumer of that event appears later, promote it to a proper event, not before.
- No multi-tenancy schema (separate DB per tenant, etc.) — single-schema, `user_id`/`profile_id`-scoped rows are sufficient for the pilot.
- No GraphQL, no BFF layer — one REST API serves both CMS and public frontend.
- No custom ORM abstraction on top of SQLAlchemy — SQLAlchemy 2.0 async is already the right level of abstraction.

---

## 3. Project Structure

```
chatfolio-be/
├── docs/
│   └── BACKEND_PLAN.md          # this file
├── src/
│   └── chatfolio/
│       ├── main.py               # FastAPI app factory, router registration
│       ├── config/
│       │   ├── settings.py       # pydantic-settings, composed sub-settings
│       │   └── logging.py
│       ├── api/
│       │   ├── deps.py           # shared Depends: db session, current_user, policies
│       │   └── v1/
│       │       ├── auth.py
│       │       ├── profiles.py
│       │       ├── cv.py
│       │       ├── ai_review.py
│       │       ├── portfolio_settings.py
│       │       ├── public_portfolio.py
│       │       ├── chat.py
│       │       └── admin.py
│       ├── services/
│       │   ├── auth_service.py
│       │   ├── profile_service.py
│       │   ├── cv_service.py
│       │   ├── generation_service.py
│       │   ├── embedding_service.py
│       │   ├── portfolio_service.py
│       │   ├── chat_service.py
│       │   ├── rag_service.py
│       │   └── admin_service.py
│       ├── repositories/
│       │   ├── base.py
│       │   ├── user_repository.py
│       │   ├── profile_repository.py
│       │   ├── cv_repository.py
│       │   ├── section_repository.py
│       │   ├── chatfolio_repository.py
│       │   ├── chat_repository.py
│       │   └── audit_repository.py
│       ├── models/                # SQLAlchemy ORM models (see §5)
│       ├── schemas/                # Pydantic request/response DTOs, mirrors api/api/v1 modules
│       ├── llm/
│       │   ├── base.py             # LLMProvider interface + LLMTask enum
│       │   ├── factory.py
│       │   ├── providers/
│       │   │   ├── deepseek.py
│       │   │   ├── openai.py
│       │   │   ├── gemini.py
│       │   │   ├── claude.py
│       │   │   ├── grok.py
│       │   │   └── openrouter.py
│       │   ├── prompts/            # versioned prompt templates per task
│       │   └── guardrails.py       # grounding checks, refusal logic
│       ├── vectorstore/
│       │   ├── base.py             # VectorStore interface
│       │   └── chroma_store.py
│       ├── storage/
│       │   ├── base.py             # StorageBackend interface
│       │   └── s3_storage.py
│       ├── cv_parsing/
│       │   ├── pdf_extractor.py
│       │   └── docx_extractor.py
│       ├── workers/
│       │   ├── worker.py           # arq worker entrypoint + job registry
│       │   ├── jobs_cv.py
│       │   └── jobs_embedding.py
│       ├── core/
│       │   ├── security.py         # password hashing, JWT encode/decode
│       │   ├── rate_limit.py
│       │   ├── exceptions.py       # domain exceptions -> HTTP mapping
│       │   └── pagination.py
│       └── db/
│           ├── session.py
│           └── base.py
├── alembic/
├── tests/
│   ├── unit/                       # services w/ fake repositories & fake LLM provider
│   ├── integration/                # real Postgres (dockerized), httpx AsyncClient
│   └── factories/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
└── Makefile
```

---

## 4. Configuration Strategy

Single composed `Settings` object built from focused sub-settings, each owning one concern — nothing hardcoded, everything overridable per environment:

```python
class DatabaseSettings(BaseSettings): ...
class RedisSettings(BaseSettings): ...
class StorageSettings(BaseSettings): ...        # provider, bucket, endpoint, credentials
class SecuritySettings(BaseSettings): ...        # jwt secret, token TTLs, cors origins
class LLMSettings(BaseSettings):
    default_provider: LLMProviderName = "deepseek"
    provider_for_extraction: LLMProviderName | None = None   # falls back to default_provider
    provider_for_chat: LLMProviderName | None = None
    provider_for_intent: LLMProviderName | None = None
    deepseek_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    grok_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
class FeatureFlags(BaseSettings):
    enable_custom_domains: bool = False   # Phase 2, stubbed now
    enable_billing: bool = False          # Phase 2, stubbed now

class Settings(BaseSettings):
    env: Literal["local", "staging", "production"] = "local"
    database: DatabaseSettings
    redis: RedisSettings
    storage: StorageSettings
    security: SecuritySettings
    llm: LLMSettings
    features: FeatureFlags
```

Rules:
- One `.env.example` documents every variable; real values never committed.
- `Settings` is instantiated once (`get_settings()`, `lru_cache`d) and injected via `Depends` — never imported as a global mutable.
- Switching LLM provider per task (default = DeepSeek everywhere per requirement) is a config edit only — no code change, no redeploy of a different image.
- Feature flags gate Phase 2 stubs (custom domains, billing) so the tables/endpoints can exist without being reachable.

---

## 5. Data Model

Matches the entity list in `Requirement.md` §Data Model Overview. All tables: `id` (UUID pk), `created_at`, `updated_at` unless noted.

| Entity | Key fields | Relationships |
|---|---|---|
| `User` | email (unique), hashed_password, role (`candidate`/`admin`), is_active | 1:1 → `CandidateProfile` |
| `CandidateProfile` | user_id (FK, unique), full_name, title, bio, location, contact_email, phone, social_links (JSONB), status (`draft`/`approved`) | 1:N Experience/Project/Skill/Education/PortfolioSection/UploadedCV; 1:1 PublicChatfolio |
| `UploadedCV` | profile_id (FK), file_url, file_type, size_bytes, status (`pending`/`processing`/`parsed`/`failed`), raw_text, parsed_json (JSONB), error_message | belongs to CandidateProfile |
| `Experience` | profile_id (FK), company, role, start_date, end_date, is_current, description | belongs to CandidateProfile |
| `Project` | profile_id (FK), title, description, tech_stack (JSONB list), impact, links (JSONB) | belongs to CandidateProfile |
| `Skill` | profile_id (FK), name, category, proficiency | belongs to CandidateProfile |
| `Education` | profile_id (FK), institution, degree, field, start_date, end_date | belongs to CandidateProfile |
| `PortfolioSection` | profile_id (FK), section_type (`intro`/`summary` — narrowed from the original 6-value list, see §16 changelog), content (text), status (`draft`/`approved`), generated_by (`ai`/`manual`), version, unique on (profile_id, section_type) | belongs to CandidateProfile |
| `PublicChatfolio` | profile_id (FK, unique), slug (unique), previous_slug (nullable, for redirects), is_published, published_at, contact_cta_config (JSONB), cv_downloadable (bool, default true per requirement) | 1:1 CandidateProfile; 1:N ChatSession. **No `subdomain` column** — derived from slug at read time (`"{slug}.chatfolio.com"`), see §16 — and no `PublicDomain` yet, deferred (Phase-2 stub, out of scope until custom domains are prioritized) |
| `PublicDomain` *(Phase-2 stub, table exists, endpoints flagged off)* | chatfolio_id (FK), domain, verification_status, ssl_status | belongs to PublicChatfolio |
| `VectorEmbedding` | profile_id (FK), source_type (`experience`/`project`/`skill`/`education`/`portfolio_section`), source_id, chunk_text, chroma_ref_id (unique, `"{source_type}:{source_id}"`), chunk_metadata (JSON) — unique on (source_type, source_id) | belongs to CandidateProfile; mirrors what's stored in Chroma for reindex/debug |
| `ChatSession` | chatfolio_id (FK), started_at, last_active_at (nullable — see §16), rapid_fire_count, is_flagged, reviewed_by_candidate. **No `recruiter_session_token` column** — the session's own UUID primary key (122 bits of entropy) already is the opaque public handle | belongs to PublicChatfolio; 1:N ChatMessage; 1:1 RecruiterMetadata |
| `ChatMessage` | session_id (FK), role (`recruiter`/`assistant`), content, intent, created_at | belongs to ChatSession |
| `RecruiterMetadata` | session_id (FK), name, company, role, required_skills, experience_expectation, location_pref, timeline, collected_at | belongs to ChatSession |
| `AdminAuditLog` | admin_id (FK), action, target_type, target_id, metadata (JSONB), created_at | belongs to User (admin) |
| `RefreshToken` *(implementation table, not in original entity list)* | user_id (FK), token_hash (unique), expires_at, revoked_at | belongs to User — see §6.1 |

Notes:
- `CandidateProfile.status` and `PortfolioSection.status` implement the "no publish without approval" core principle from the requirement — `PublicChatfolio.is_published` can only flip to `true` when profile status is `approved` (enforced in `portfolio_service`, not just at the DB layer).
- `VectorEmbedding` rows are metadata pointers, not the vectors themselves — actual vectors live in Chroma, keyed by `chroma_ref_id`. This keeps Postgres the source of truth for "what should be embedded" and makes a full reindex possible from Postgres alone.

---

## 6. Domain Modules & Functional Scope

### 6.1 Auth & User Management
- Register (email/password), login (JWT access + refresh), refresh, logout (refresh revocation).
- Password hashing via argon2.
- Role: `candidate` (default) or `admin`.
- Protected-route dependency (`get_current_user`), admin-only dependency (`require_admin`).

### 6.2 Candidate Profile (CMS core)
- CRUD for `CandidateProfile` and child entities (Experience, Project, Skill, Education).
- Manual builder = same endpoints as edit-after-CV — one data model serves both entry paths (DRY: no separate "manual profile" schema).
- Ownership enforced via `require_owner(profile_id)` policy dependency.

### 6.3 CV Upload & Parsing Pipeline
- Upload endpoint: validates type (PDF/DOC/DOCX) and size (≤20MB per requirement) before accepting.
- File stored via `StorageBackend` adapter; DB row created with status `pending`.
- Enqueues `parse_cv_job` (arq) → extract text → call LLM extraction provider → store `raw_text` + `parsed_json`, status → `parsed` or `failed` with `error_message`.
- Status polling endpoint for CMS to show parsing progress.
- Retry endpoint re-enqueues the same job idempotently.

### 6.4 AI Generation & Review
- Generate draft `PortfolioSection`s from parsed CV or manual data (intro, summary, per-section).
- Edit endpoint (candidate overrides AI text).
- Regenerate endpoint for a single section (re-runs generation, bumps `version`).
- Approve endpoint — flips section (and eventually profile) status to `approved`; **this is the trigger point for embedding generation** (§6.5).

### 6.5 Vector Embeddings
- On section/profile approval: chunk approved content, call embedding model, upsert into Chroma via `VectorStore` adapter, write pointer rows to `VectorEmbedding`.
- On edit-after-approval: re-approval re-triggers chunk+embed for the changed section only (not a full profile re-embed — efficiency, not premature optimization: it's the natural unit of change).

### 6.6 Portfolio Settings & Publish
- Publish/unpublish (`PublicChatfolio.is_published`), gated on profile approval status.
- Slug edit with uniqueness validation; subdomain pattern `{slug}.chatfolio.com` derived from slug.
- Old-slug redirect: keep a `PublicChatfolioSlugHistory`-style lookup (or simple `previous_slug` column) so links don't 404 after a rename — small addition, directly required ("Redirect old public URLs when a slug changes if possible").
- Contact CTA config, CV downloadable toggle (defaults public, per requirement decision).
- `PublicDomain` endpoints exist but return 404/disabled unless `features.enable_custom_domains` is on.

### 6.7 Public Portfolio API (read-only, no auth)
- Resolve by slug / subdomain → structured profile (only `approved` + `published` data — never draft content).
- 404 for unpublished/unknown slugs — no leakage of existence vs. non-existence beyond a generic not-found.

### 6.8 Chat / RAG (public, no login)
- Start session (per chatfolio) → session token issued (opaque, stored client-side, not a JWT — no auth implied).
- Send message → pipeline:
  1. Intent classification (fast/cheap LLM call, `LLMTask.INTENT`).
  2. Retrieval: similarity search in Chroma scoped to `profile_id` (never cross-candidate).
  3. Grounded generation: system prompt (guardrails, first-person voice) + retrieved chunks + windowed recent history → `LLMTask.CHAT` provider.
  4. Low-similarity fallback: below a configured threshold, respond with the "I don't have that information..." template rather than letting the LLM guess (§6.9).
  5. Lightweight recruiter-context extraction pass (optional, best-effort) merges into `RecruiterMetadata` when the recruiter volunteers info.
- All messages persisted (`ChatMessage`), visible only to profile owner + admin.
- Rate limiting per session/IP (§6.10).

### 6.9 Guardrails (implementation, not just policy)
- System prompt is built exclusively from `approved` content — draft/unapproved sections are never included in the retrieval or prompt context.
- Retrieval similarity threshold below which the model is not shown the low-confidence chunk at all (prevents "creative filling").
- Explicit prompt instructions: no salary/availability/notice-period claims unless present verbatim in approved profile data; no third-person slip; no fabricated contact promises.
- Post-generation check (cheap heuristic, not another LLM call): if response contains no supporting retrieved chunk and isn't the canned fallback, log for admin review (quality signal, not a hard block — avoids over-engineering a classifier for the pilot).

### 6.10 Rate Limiting & Abuse Prevention
- `slowapi` + Redis: per-IP and per-session limits on chat send and auth endpoints.
- Session-level cool-down on rapid-fire messages.
- Abuse flag on session (excessive rate-limit hits) surfaced to admin, not auto-ban in MVP (moderation stays human-reviewed for the pilot — see admin scope).

### 6.11 Admin APIs
- List users, list Chatfolios (with publish/status filter).
- Usage metrics: chat counts, CV parse success/failure rate, per-candidate counts.
- Failed CV parsing job review (with retry trigger).
- Audit log of admin actions (every admin mutation writes an `AdminAuditLog` row — cheap, and required by the requirement's moderation-readiness note).
- Moderation: for the pilot, admin can unpublish a Chatfolio or flag a chat session; no automated content moderation model in MVP (YAGNI until abuse volume justifies it) — recommend this as the pilot's moderation level given free/low-volume launch.

---

## 7. LLM Provider Abstraction

```python
class LLMTask(str, Enum):
    EXTRACTION = "extraction"      # CV -> structured data
    GENERATION = "generation"      # portfolio section drafting
    INTENT = "intent"              # recruiter intent classification
    CHAT = "chat"                  # RAG response generation
    EMBEDDING = "embedding"        # vector generation

class LLMProvider(Protocol):
    async def complete(self, *, system: str, messages: list[Message], **kwargs) -> str: ...

class LLMProviderFactory:
    def for_task(self, task: LLMTask) -> LLMProvider:
        provider_name = self._settings.llm.provider_for(task)  # falls back to default_provider
        return self._registry[provider_name]
```

- One `.providers.<name>` module per vendor, each implementing the same `LLMProvider` protocol against that vendor's SDK/HTTP API.
- Default provider: DeepSeek (per requirement). GPT/Gemini/Claude/Grok/OpenRouter are registered as config values but only get a provider class (and are only instantiated) once a task actually needs one — no speculative stub classes for vendors nothing calls yet.
- Per-task override means, e.g., extraction could later run on a cheaper/faster model than chat without touching service code.
- **Embeddings do not go through this abstraction.** `LLMTask.EMBEDDING` and `provider_for_embedding` exist in config as a future extension point, but DeepSeek has no embeddings API, so Phase 7 uses Chroma's bundled local ONNX model (`vectorstore/local_embedder.py`) instead — no API key, no network call, no per-embedding cost, and it's fully decoupled from `LLMProviderFactory`. Swapping to a hosted embedding provider later means changing only that one module.

---

## 8. Background Jobs (arq)

| Job | Trigger | Idempotency key |
|---|---|---|
| `parse_cv_job` | CV upload / retry | `cv_id` (re-running just re-processes the same row) |
| `embed_content_job` | Experience/Project/Skill/Education create or update; approved `PortfolioSection` | `chroma_ref_id = "{source_type}:{source_id}"` — Chroma `upsert` on this id replaces the vector in place, and the Postgres `VectorEmbedding` pointer row is looked up by the same id, so re-running with new `chunk_text` is a plain overwrite, not a duplicate |

`generate_sections_job` from the original plan didn't end up existing: portfolio-section generation (§6.4) turned out to be cheap and fast enough to run synchronously in the request path (`GET /api/v1/sections`, `POST .../regenerate`) rather than needing a background job — only CV parsing and embedding are slow/expensive enough to justify one.

Both jobs pull their dependencies (`sessionmaker`/`storage`/`llm_factory`/`vector_store`/`embed_texts`) from arq's `ctx` dict, populated once in `workers/worker.py`'s `on_startup` — this is what makes each job a plain function testable by calling it directly with a hand-built `ctx`, no real worker process or network call needed in the test suite (see §11).

Worker process runs as a separate container (`workers/worker.py`), sharing the same `models`/`services`/`llm` code as the API — no duplicated logic between request path and job path.

---

## 9. API Surface Map (v1)

```
POST   /api/v1/auth/register
POST   /api/v1/auth/login                      # may return a 2FA challenge instead of tokens
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
GET    /api/v1/auth/me
POST   /api/v1/auth/forgot-password
POST   /api/v1/auth/reset-password
POST   /api/v1/auth/2fa/setup                  # requires auth
POST   /api/v1/auth/2fa/verify-setup           # requires auth
POST   /api/v1/auth/2fa/login/verify           # no auth; redeems a login challenge_token
POST   /api/v1/auth/2fa/login/resend           # no auth; redeems a login challenge_token

GET    /api/v1/profiles/me
PATCH  /api/v1/profiles/me
POST   /api/v1/profiles/me/experience        (+ GET/PATCH/DELETE /{id})
POST   /api/v1/profiles/me/projects          (+ GET/PATCH/DELETE /{id})
POST   /api/v1/profiles/me/skills            (+ GET/PATCH/DELETE /{id})
POST   /api/v1/profiles/me/education         (+ GET/PATCH/DELETE /{id})

POST   /api/v1/cv/upload
GET    /api/v1/cv/{id}/status
POST   /api/v1/cv/{id}/retry

GET    /api/v1/sections                       # list draft/approved sections
PATCH  /api/v1/sections/{id}                   # edit content
POST   /api/v1/sections/{id}/regenerate
POST   /api/v1/sections/{id}/approve

GET    /api/v1/portfolio-settings
PATCH  /api/v1/portfolio-settings              # slug, cta, cv visibility, theme
POST   /api/v1/portfolio-settings/publish
POST   /api/v1/portfolio-settings/unpublish

GET    /api/v1/public/chatfolio/{slug}         # no auth; resolves slug or subdomain
GET    /api/v1/public/chatfolio/{slug}/cv      # download, if enabled

POST   /api/v1/public/chat/{slug}/sessions
POST   /api/v1/public/chat/sessions/{session_id}/messages
GET    /api/v1/dashboard/conversations          # candidate-owned, authed
GET    /api/v1/dashboard/conversations/{id}
POST   /api/v1/dashboard/conversations/{id}/mark-reviewed

GET    /api/v1/admin/users
GET    /api/v1/admin/chatfolios
GET    /api/v1/admin/metrics
GET    /api/v1/admin/cv-jobs/failed
POST   /api/v1/admin/cv-jobs/{id}/retry
POST   /api/v1/admin/chatfolios/{id}/unpublish
```

---

## 10. Security & Privacy

- Argon2 password hashing; JWT access token short-lived (~15 min), refresh token longer-lived (30 days) and rotated on every use (old token revoked, reuse of a revoked token is rejected outright — catches token theft/replay). **Actual transport**: both tokens are returned in the JSON response body from `/auth/login` and `/auth/refresh` — the backend does not set any cookie. A frontend that stores the refresh token in `localStorage`/`sessionStorage` exposes it to theft via any XSS on that origin; this doc previously claimed httpOnly-cookie storage, which nothing in the code actually does (only a `Set-Cookie` response header can create an httpOnly cookie — client JS cannot). Until a cookie-based flow is deliberately built, the safer pattern for a web frontend is: access token in memory only (never persisted), refresh token in the most XSS-resistant storage available to it, and a short access-token TTL (already 15 min) to bound the blast radius of either leaking. See the Admin Panel UI reference doc for the concrete pattern this project's frontend should follow.
- CORS restricted to known frontend origins per environment.
- File upload: type allow-list + 20MB size cap enforced before write, not after — plus a file-signature (magic-bytes) check (`CVService._MAGIC_BYTES`), since `content_type` and the filename extension are both fully client-controlled and prove nothing about the actual bytes on their own.
- Every endpoint that triggers a real LLM call is rate-limited, not just the public chat path: `POST /cv/upload` and `/cv/{id}/retry` (10/hour), `POST /sections/{id}/regenerate` (10/hour) — found missing during the 2026-08-21 security pass (§16) and fixed the same day. `GET /sections` is exempt: it only calls the generation LLM for section types that don't exist yet, so it self-limits to at most two calls ever per candidate, then becomes a plain read.
- Custom domain input is validated against an RFC-1123 hostname pattern (`schemas/domain.py`), not accepted as an arbitrary string — added in the same pass.
- CV downloads and file storage: private bucket, served via short-lived presigned URLs — never a public-by-default bucket, even though the *download feature* defaults to publicly visible per requirement (the bucket stays private; visibility is an application-level gate, not an S3 ACL).
- Ownership checks on every candidate-scoped mutation (`require_owner`).
- Public endpoints never read from unapproved/draft tables.
- Chat logs restricted to owning candidate + admin (enforced at repository query level: always filtered by profile ownership).
- Admin actions logged (`AdminAuditLog`).
- Data export/delete: not built in MVP, but `CandidateProfile` + related tables are already scoped by a single FK chain from `user_id`, so a future "export/delete everything for this user" job is a straightforward cascading query — no schema rework needed later.
- Security headers on every response (`SecurityHeadersMiddleware`): `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security`. No CSP — pure JSON API, no HTML responses.
- Error tracking (Sentry) and metrics (`/metrics`, Prometheus) are both opt-in via env config, off by default in local/test — see §12 config.
- Dependencies checked with `pip-audit` during Phase 13's hardening pass; findings and reasoning logged in §16.

---

## 11. Testing Strategy

- **Unit** — services tested against fake repositories and a fake `LLMProvider`/`VectorStore` (no network, no DB). Fast, run on every commit.
- **Integration** — real Postgres (docker), real Redis, `httpx.AsyncClient` against the FastAPI app; LLM providers mocked at the HTTP layer (no real API spend in CI).
- **Guardrail fixtures** — a small fixed set of recruiter questions with known "not in profile" answers, asserted against the fallback template, to catch guardrail regressions.
- **Test isolation** — the DB engine is process-lifetime `lru_cache`d and bound to whatever event loop first created it, so pytest-asyncio must run fixtures and tests on one shared loop (`asyncio_default_fixture_loop_scope = "session"` / `asyncio_default_test_loop_scope = "session"` in `pyproject.toml`) — per-test loops break asyncpg connections. Data isolation between tests is a `conftest.py` autouse fixture that `TRUNCATE`s every table in `Base.metadata` after each test; it reads the table list from the ORM metadata (not a hardcoded list) so it never needs updating as new models land.
- **Tests must never point at the dev database.** `tests/conftest.py` forces `DATABASE_NAME` to `chatfolio_test` (overridable via `TEST_DATABASE_NAME`) *before* anything imports `chatfolio.main` (which builds the app, and therefore reads settings, at import time), and both the schema-setup and truncate fixtures hard-`assert "test" in settings.database.name` as a second line of defense. This is load-bearing, not decorative: early in this project's history the test suite ran against the same `chatfolio` database backing the locally running `api` container, so every `pytest` run silently deleted whatever the developer had registered through the actual app. `docker/postgres/init-test-db.sql` auto-provisions `chatfolio_test` on a fresh volume; CI's Postgres service creates it directly via `POSTGRES_DB`. If a new test database name or a second test DB is ever introduced, keep the assertion — don't relax it for convenience.
- **The same rule applies to object storage.** `tests/conftest.py` forces `STORAGE_BUCKET` to `chatfolio-cv-test` (overridable via `TEST_STORAGE_BUCKET`), asserted the same way, with a session-scoped fixture that empties the test bucket before *and* after the run. Discovered this was needed the same day as the DB issue: CV upload tests were writing real objects into the same `chatfolio-cv` bucket the dev `api` container serves from — non-destructive (nothing gets deleted), but it would have silently accumulated test files in the dev bucket forever. Any future resource a test touches (a queue, a cache namespace, a second bucket) gets the same treatment: dedicated name, asserted before touching it, cleaned up after.
- **...and to the job queue.** `tests/conftest.py` forces `REDIS_DB` to `1` (overridable via `TEST_REDIS_DB`), asserted via `settings.redis.db != 0` (0 is the dev/worker default — Redis db indices are numeric, so there's no `"test"`-substring check to reuse), with a session-scoped fixture that `FLUSHDB`s that index before and after the run. Found by actually running the real arq worker against the dev stack during Phase 5 verification: CV upload tests enqueue `parse_cv_job`, and before this fix they enqueued into the same Redis db the dev worker consumes from — a real dev worker would have picked up jobs referencing rows that only exist in `chatfolio_test`, logged `cv_not_found`, and moved on (harmless, but still cross-environment bleed, and it *was* observed happening live). Also caught in the same pass: `RedisSettings.db` existed as a config field since Phase 1 but was never actually passed to `ArqRedisSettings` in `workers/queue.py` or `workers/worker.py` — both always connected to db 0 regardless of config. Fixed alongside the test-isolation change.
- **...and to the vector store (and the local embedder).** No test needs a real Chroma connection or the real local ONNX embedding model: `tests/conftest.py` sets global `app.dependency_overrides` for both `get_vector_store` (→ `FakeVectorStore`) and `get_embed_texts` (→ a fixed-vector stub) for the whole session. The only *synchronous* vector-store calls in any request path are `EmbeddingService.delete_embed()` (section edit/regenerate, child delete) and `RAGService.retrieve()` (public chat) — every create/update path just enqueues a job that's never executed in tests (no worker runs during `pytest`, same as `parse_cv_job`). `embed_content_job` itself is tested directly with a hand-built `ctx` and a fake embedder, same pattern as `parse_cv_job`. **Pitfall already hit once**: a per-test fixture that further overrides `get_vector_store` (e.g. to spy on calls) must restore a *fake* on teardown, never `.pop()` the override to nothing — popping removes conftest's global default too, and every test that runs afterward in the same session silently falls through to a real Chroma connection. Found via 3 unrelated tests failing only when run after `test_embedding_triggers.py`.
- **...and to slowapi's rate limiter (Phase 9).** Its per-IP counters live in the same Redis db as the arq queue, keyed by client IP — every test's `httpx.ASGITransport` client shares one IP, so counters silently accumulate *across unrelated tests* if only flushed once per session. The Redis-flush fixture that started as session-scoped (Phase 5) had to become function-scoped once Phase 9 added rate-limited endpoints, specifically because of this — a session-scoped flush let chat tests earlier in the run eat into the rate-limit budget of chat tests later in the run, causing spurious 429s nowhere near whichever test actually sent the 16th message.
- **`Base.metadata.create_all()` (used to build the test schema) does not handle schema evolution** — it only creates tables that don't already exist yet, it never `ALTER`s one to match a changed model. This is invisible in CI (every run starts from an empty test database) but bites hard in an interactive dev session: after changing a column on an existing model (e.g. Phase 9's `last_active_at` nullability fix), the *test* database still has the old shape until its stale table is manually dropped (`DROP TABLE ... CASCADE`) so `create_all` rebuilds it — the symptom is a confusing `NotNullViolationError` (or similar) that has nothing to do with the code change that triggered it. The dev database doesn't have this problem since it goes through real Alembic migrations; only the test database's `create_all`-based bootstrap does.
- Coverage priority: services > repositories > routers (routers are thin, so they need less direct coverage once the service beneath them is tested).

---

## 12. Dev Environment & Tooling

- `docker-compose.yml`: `api`, `worker`, `postgres`, `redis`, `chroma`, `minio`.
- `Makefile`: `make up`, `make migrate`, `make test`, `make lint`.
- Pre-commit: `ruff check`, `ruff format`, `mypy`.
- CI (GitHub Actions): lint → typecheck → unit tests → integration tests (compose services as CI service containers) → build image.

---

## 13. Build Phases (execution order)

1. **Scaffolding** — repo layout, `Settings`, health check, Docker Compose, CI skeleton.
2. **Auth & Users** — register/login/JWT, role support, protected-route dependency.
3. **Profile Core** — CandidateProfile + Experience/Project/Skill/Education CRUD (serves both manual builder and post-CV editing).
4. **Storage & CV Upload** — `StorageBackend` adapter, upload endpoint, validation, status tracking.
5. **CV Parsing Pipeline** — text extraction, `parse_cv_job`, LLM extraction provider (DeepSeek first).
6. **AI Generation & Review** — section generation, edit, regenerate, approve.
7. **Embeddings** — `VectorStore`/Chroma adapter, chunking, `embed_content_job` on approval.
8. **Publish & Public API** — slug/subdomain resolution, publish gating, public read endpoints.
9. **Chat/RAG** — session start, intent classification, retrieval, grounded generation, guardrail fallback.
10. **Recruiter Context Capture** — extraction pass, `RecruiterMetadata`, candidate-facing conversation views.
11. **Rate Limiting & Abuse Signals** — `slowapi`, session cool-downs, abuse flags.
12. **Admin APIs** — user/chatfolio listing, metrics, failed-job review, audit log.
13. **Hardening** — Sentry, Prometheus, security pass, load test the chat path.
14. **Phase-2 stubs (flagged off)** — `PublicDomain` table + disabled endpoints, plan/billing field placeholders on `User`.

Each phase should ship with tests before moving to the next — no phase depends on a later one, so this order can also be the PR sequence.

---

## 14. Open Decisions (from Requirement.md, resolved)

- Vector DB: **Chroma**.
- LLM: **DeepSeek default**, GPT/Gemini/Claude/Grok/OpenRouter configurable per §7.
- CV upload limit: **20MB**.
- CV download visibility: **public by default**, candidate can still toggle via `cv_downloadable`.
- Recruiter context collection: **optional**, never blocks chat.
- Admin moderation level for pilot: **manual review** — admin can unpublish/flag, no automated moderation model (§6.11). Revisit once real abuse volume data exists.

---

## 15. Future Extension Points (Phase 2, already accounted for in schema/architecture)

- Billing/plans: add `plan` + `usage_limits` to `User`/`CandidateProfile`; enforcement point is already centralized in services (e.g., `chat_service` is the single place a message-count check would go).
- Custom domains: `PublicDomain` table and adapter interface already exist behind `features.enable_custom_domains`.
- Multiple themes: `PublicChatfolio` already carries a config JSONB column for this.
- Advanced analytics: `ChatMessage`/`ChatSession` already capture the raw data; analytics is a read-model/reporting concern on top, not a schema change.
- Team/company accounts: would introduce an `Organization` entity owning multiple `User`s — deferred; not designed in now (YAGNI) since it changes the ownership model and shouldn't be half-built speculatively.

---

## 16. Changelog

- **2026-08-26** — Step 2 of the `Required_API_Doc.md` gap-closure plan: real LLM token-usage
  tracking, groundwork for §2/§6's analytics endpoints (not yet exposed anywhere — purely internal
  capture for now, confirmed with the user before starting given it changes an internal interface
  shared by every LLM call site).
  - **`LLMProvider.complete()` now returns `LLMCompletion(content, tokens_used)`** instead of a
    bare `str` (`llm/base.py`). `DeepSeekProvider` (the only real provider that exists) reads
    `usage.total_tokens` off the response — an OpenAI-compatible field DeepSeek already returns on
    every completion, previously just discarded. All 4 call sites updated
    (`generation_service.py`, `rag_service.py`'s intent-classification and reply-generation calls,
    `workers/jobs_cv.py`) to unpack `.content` where the text was used and `.tokens_used` to
    increment a new `CandidateProfile.ai_tokens_used` column (new migration `b40fe3e66d6d`,
    `server_default='0'` since the table already has real rows). `RAGService.classify_and_extract`
    and `.generate_reply` both changed from returning a bare value to a tuple with the token count
    appended — `ChatService.send_message` (their only caller) sums both into one increment per
    chat turn, since a single recruiter message triggers two separate LLM calls (intent
    classification, then the reply itself).
  - **`tests/factories/fake_llm.py`** updated in the same change (`FakeLLMProvider`/`FakeLLMFactory`
    gained an optional `tokens_used` param, defaulting to `0`) — this is the one place the test
    suite touches the interface, so every existing chat/generation/CV test kept passing unchanged
    rather than breaking on the return-type change.
  - **Deliberately cumulative, not reset monthly** — a true "this month's usage" would need a
    scheduled reset job (arq supports cron; nothing in this codebase uses it yet). Shipping the
    simpler cumulative-since-signup counter now rather than guessing at reset semantics nobody
    asked for; flagged explicitly to the user as a known limitation rather than a silent decision.
  - Verified live against the real DeepSeek API, not just the fake: a fresh candidate's
    `ai_tokens_used` was `0`, triggering real section generation (`GET /sections`) via curl moved
    it to `410`, matching DeepSeek's actual reported usage for those two completions. Full suite
    (101 tests, all passing — no behavior change, only added tracking) plus `ruff`/`mypy` clean.
    `ADMIN_PANEL_UI_REFERENCE.md`'s `GET /admin/metrics` section gained a forward-looking note
    that this field exists internally but isn't in any response yet, so nobody builds UI against
    it prematurely.

- **2026-08-26** — Step 1 of the `Required_API_Doc.md` gap-closure plan: renamed every route's
  prefix from `/v1` to `/api/v1` (hard cutover, confirmed with the user — `/v1` is not dual-mounted
  and now 404s outright). Changed `main.py`'s eleven `app.include_router(..., prefix=...)` calls,
  the one hardcoded path outside that mechanism (`public_portfolio.py`'s slug-rename
  `RedirectResponse`, which would otherwise have silently kept redirecting to a dead `/v1` URL
  post-rename), and every integration test's hardcoded path (192 call sites across 15 test files).
  Reformatted the handful of test lines that overflowed the 100-char limit once `/api/v1` made
  them longer (`ruff format`, mechanical wrapping only — verified via diff that nothing but
  whitespace/line-breaks changed). All three frontend reference docs and `Required_API_Doc.md`
  itself updated to match. Verified live: `curl localhost:8000/v1/health` now `404`s,
  `curl localhost:8000/api/v1/health` returns `200`, same for a real `POST .../auth/login` (`404`
  under the old prefix, correct `401` for bad credentials under the new one). Full suite (101
  tests, unchanged pass count — only paths moved, no behavior changed) plus `ruff`/`mypy` clean.
  This is the first of several steps in a larger plan to close every gap in
  `Required_API_Doc.md`; see that doc and this changelog's later entries for the rest.

- **2026-08-25** — Fixed `GET /dashboard/conversations` (`DashboardService.list_conversations`)
  listing every session for a chatfolio, including ones where a recruiter opened the chat widget
  (creating a `ChatSession`) but never actually sent a message — clutter with no content behind
  it. The in-progress filter attempting to fix this, `.where(ChatSession.messages.count() > 0)`,
  wasn't valid SQLAlchemy at all (`.count()` isn't a thing on a relationship attribute used this
  way) and would have raised at request time rather than just filtering wrong. Replaced with
  `.where(ChatSession.messages.any())`, which SQLAlchemy compiles to a real `EXISTS` subquery —
  exactly "has at least one message," and pairs correctly with the existing `_with_message_counts`
  helper and the `limit`/`offset` pagination, which now apply after the empty sessions are
  excluded rather than before. Verified live: created a session via
  `POST /public/chat/{slug}/sessions` with no follow-up message — `GET /dashboard/conversations`
  now correctly returns `[]` for it, while a session that did get a message still returns with
  the correct `message_count`. Added `test_list_conversations_excludes_sessions_with_no_messages`;
  full suite (101 tests) plus `ruff`/`mypy` clean. `ADMIN_PANEL_UI_REFERENCE.md` §7 updated to
  state this guarantee explicitly (`message_count` is never `0` in this list).

- **2026-08-23** — Built three previously-nonexistent auth journeys, requested to match a
  frontend design reference (forgot/reset password, email-or-phone-or-both 2FA): forgot-password
  email → reset-password link, and 2FA enrollment + login-challenge verification. None of this
  existed at all before today — no email/SMS-sending infrastructure existed anywhere in the
  backend, so this is new surface, not a fix.
  - **New `notifications/` package** (`base.py`'s `EmailSender`/`SmsSender` protocols,
    `smtp_email.py`, `sms_vendor.py`) — mirrors the existing pluggable-provider pattern already
    used for LLM providers (`llm/factory.py`) rather than hardcoding a single vendor. `SmtpEmailSender`
    sends real mail via `aiosmtplib` against any SMTP host (Gmail app password, SES/Mailgun SMTP,
    etc.); `VendorSmsSender` is a generic REST POST adapter (`{"to", "message", "sender_id"}` JSON
    with a Bearer `api_key`) since the user's own SMS vendor's exact contract wasn't specified —
    swap the payload shape in that one file if a real vendor's contract differs. Both fall back to
    logging-and-skipping (via `structlog`, not raising) when unconfigured (`EMAIL_SMTP_HOST` /
    `SMS_API_URL` unset) — verified live: with no SMTP configured, `/auth/forgot-password` still
    returns a correct `204` and logs `email.smtp_not_configured` instead of failing the request.
    Real credentials go in `.env`'s new `EMAIL_*`/`SMS_*`/`APP_FRONTEND_BASE_URL` block; tests
    never touch either channel (`tests/factories/fake_notifications.py`, overridden globally in
    `conftest.py` like the vector-store fake).
  - **`PasswordResetToken`** (new model + migration `9ed9941a563a`) — single-use opaque token,
    hash-only at rest, 30-min TTL (`SECURITY_PASSWORD_RESET_TOKEN_TTL_MINUTES`), modeled directly
    after the existing `RefreshToken` shape. `POST /auth/forgot-password` always returns `204`
    whether or not the email is registered (no user-enumeration via response shape/timing
    branch), and `POST /auth/reset-password` revokes every outstanding refresh token for that
    account on success — a password reset now also signs out every other session, not just the
    one performing the reset.
  - **`OtpCode`** (new model, same migration) — 6-digit codes for both 2FA enrollment and 2FA
    login, hash-only at rest, 10-min TTL, capped at 5 wrong attempts before a fresh code is
    required. `User` gained `phone`, `two_factor_enabled`, `two_factor_method` (`email` / `phone`
    / `both`). Login (`AuthService.login`) now returns one of two shapes discriminated by a new
    `requires_two_factor` field — normal `TokenResponse` unchanged for accounts without 2FA, or a
    `TwoFactorChallengeResponse` (a short-lived 5-minute JWT `challenge_token`, distinct
    `TokenType.TWO_FACTOR_CHALLENGE` so it can never be replayed as a real access token) for
    accounts with it enabled. New endpoints: `POST /auth/2fa/setup` (request a code, not yet
    enabled), `POST /auth/2fa/verify-setup` (confirms and flips `two_factor_enabled`), `POST
    /auth/2fa/login/verify` (redeems the challenge for real tokens), `POST /auth/2fa/login/resend`
    (invalidates the previous code, issues a new one). `"both"` sends the *same* code to both
    channels rather than two independent codes, so one submitted code satisfies either.
  - Renamed `hash_refresh_token`/`generate_refresh_token` → `hash_opaque_token`/
    `generate_opaque_token` in `core/security.py` since the same sha256-hash-a-bearer-secret
    pattern is now shared by refresh tokens and password-reset tokens; not worth two near-
    identical functions with token-type-specific names for what's structurally one operation.
  - Verified end-to-end against the real running stack: rebuilt the containers
    (`docker compose down; docker compose up -d --build`), autogenerated and hand-fixed the
    migration (Alembic's autogenerated `ADD COLUMN ... two_factor_method` didn't create the
    Postgres enum type first the way `CREATE TABLE` does — added an explicit
    `sa.Enum(...).create(checkfirst=True)` before the `add_column`, confirmed by reproducing the
    `UndefinedObjectError` and then the successful upgrade), applied it, then curled
    register → forgot-password → 2FA setup live against `localhost:8000` and confirmed the
    correct masked-destination and log-and-skip behavior. Full suite (100 tests, 12 new) plus
    `ruff`/`mypy` clean. `ADMIN_PANEL_UI_REFERENCE.md` §2 gained the full request/response
    documentation for all six new endpoints plus the changed `/auth/login` response shape; the
    rate-limit summary table updated to match.

- **2026-08-22** — Audited `ADMIN_PANEL_UI_REFERENCE.md` against the live OpenAPI schema (`curl .../openapi.json`, not just re-reading router source) rather than trust-but-don't-verify. Every real CMS-facing endpoint (44 total across auth/profile/cv/sections/portfolio-settings/custom-domain/dashboard/admin) was already documented — found the opposite problem instead: the doc claimed `GET /profiles/me/{experience|projects|skills|education}/{id}` exists, and it doesn't — only list/create/update/delete were ever registered for those four resources (`api/api/v1/profiles.py`'s `register_child_routes` factory never wired up a single-item GET). Corrected the doc to state plainly that there's no per-item fetch, so a frontend doesn't build a flow around an endpoint that would 404.

- **2026-08-22** — Added `recruiter_count` to `GET /public/chatfolio/{slug}` — count of distinct chat sessions where the recruiter volunteered their name or company at some point (`PublicPortfolioService.count_identified_recruiters`, a single joined `COUNT` against `RecruiterMetadata`/`ChatSession` filtered by `chatfolio_id`, not a raw session/visit count). Deliberately `name OR company`, not `AND` — either one alone is enough to say "a real recruiter engaged," and requiring both would undercount every recruiter who only gave one. Verified live against real dev data: 6 of 8 `recruiter_metadata` rows for the test chatfolio have a non-null name or company, matching the endpoint's returned count exactly. `PUBLIC_CHAT_UI_REFERENCE.md` updated with the field and an explicit warning not to present it as a page-view/session count — it's a meaningfully smaller, more specific number (most chat sessions never get a recruiter to volunteer who they are).

- **2026-08-22** — Fixed a frontend-blocking gap: `POST /public/chat/sessions/{id}/messages` always returned `"intent": null`, even though intent classification runs on every message. Root cause was simply that `ChatService.send_message` only ever set `.intent` on the *recruiter's own* `ChatMessage` row — the assistant reply, which is the object actually serialized and returned to the caller, never had it set. Now both messages carry the same classified intent for that turn. Verified live: a skill question now returns `"intent": "skill_inquiry"`; an off-topic question returns `"intent": "unknown"` (classification always yields a value — `unknown` on ambiguous/off-topic input or a classifier failure — so `intent` is now populated on every response, never null). Updated `PUBLIC_CHAT_UI_REFERENCE.md`'s example payloads and the note that previously said "`intent` always null on the assistant message; irrelevant to the UI" — it's the opposite now, that's the field the frontend keys widget behavior off.

- **2026-08-22** — Found and permanently fixed the actual root cause behind every "Chroma has lost its embeddings" incident in this project's history (there had been three by this point, each blamed on a different proximate cause — stale pre-editable-install code, a wrong distance metric, an empty volume after manual troubleshooting): **the `chroma` service in `docker-compose.yml` mounted its named volume at the wrong path.** Chroma's own startup log states its persist path explicitly — `persist_path: "/data"`, `Saving data to: /data"` — but the volume was mounted at `/chroma/chroma`, a path the server never writes to. Every embedding was landing on the container's ephemeral layer the entire time; it only ever *looked* durable because every previous troubleshooting session in this project restarted `api`/`worker` to fix things but never `chroma` itself, so the same long-lived container process kept its since-boot data by accident. The user's exact repro (`docker compose down; docker compose up -d --build`, which recreates every container including `chroma`) reliably wiped it. Found by writing a marker file directly into the named volume and proving *the volume itself* survives a full `down`/`up --build` cycle intact — meaning the data loss was never about the volume being removed, only about Chroma never having written to it in the first place. Fixed the one-line mount path (`chroma_data:/data`, not `chroma_data:/chroma/chroma`); verified by reproducing the user's exact command sequence twice and confirming the embedding count survives both times.
  - **Added a self-healing safety net on top of the fix**, at the user's request, for defense-in-depth against any *other* future way Chroma's data could go missing: `reconcile_missing_embeddings()` (`services/embedding_service.py`) runs automatically on every worker startup (`workers/worker.py`), diffing every `VectorEmbedding` pointer row in Postgres against what Chroma actually has stored (`VectorStore.list_ids()`, new protocol method) and re-enqueuing `embed_content_job` for any pointer whose vector has gone missing — straight from the pointer's already-stored `chunk_text`, no need to re-derive it from the original Experience/Project/Skill/Education row. Cheap (one Postgres query, one Chroma id listing, an in-memory set diff) so it's safe to run unconditionally on every startup, not gated behind a flag. Also exposed as `scripts/backfill_embeddings.py --reconcile` for on-demand use without restarting the worker. **This safety net immediately proved itself live**, independent of the volume-mount bug: it caught 2 of 14 pointers (the AI-generated intro/summary `portfolio_section` embeddings) that had gone missing from an unrelated cause earlier in this same debugging session (a collection recreation during the mount-path investigation that only got backfilled for Experience/Project/Skill/Education, since `scripts/backfill_embeddings.py`'s normal mode only knows about `EMBEDDABLE_CHILD_TYPES` and not sections) — reconciled them automatically on the very next worker restart with zero manual action.

- **2026-08-22** — Fixed a live user-reported UX bug: recruiter chat answered a one-line question ("Rate yourself in terms of skills") with a multi-section markdown essay — headers, bold labels, nested bullet groups ("Best (Highest Confidence)", "Excellent (Very Strong)", "Additional Strengths", "Overall Confidence") — when a two-sentence answer was what the question called for. Root cause was simply that `CHAT_SYSTEM_PROMPT_TEMPLATE` (`llm/prompts/chat.py`) said "Be professional, concise, and recruiter-friendly" and nothing more specific — "concise" alone wasn't enough of a constraint for the model to resist reaching for a structured breakdown given a rich profile context to draw from. Rewrote the prompt with explicit, concrete rules: default to 1-3 sentences of plain prose, no headers/bold/lists unless the recruiter's question itself asks for a list/comparison/breakdown, lead with the direct answer rather than padding with every available fact. Verified live against the real DeepSeek model with the exact reported question plus two contrast cases: "what's your strongest skill?" now gets one sentence; "list all your technical skills with proficiency levels" correctly still gets an actual bulleted list (the prompt's carve-out working as intended, not accidentally suppressing lists altogether); "tell me about your work experience" gets a short two-sentence summary instead of a full narrative. No test changes needed — nothing asserts the exact prompt text, and no test drives real LLM output (all chat tests use `FakeLLMProvider`).

- **2026-08-21** — Post-Phase-14 security & best-practices review. Grepped for raw-SQL/string-interpolation injection risk (none — every query goes through SQLAlchemy's `select()`), secret/token logging (none), `debug=True` (none), and Host-header trust (none). Checked CORS (origins list is already environment-scoped, not wildcarded), presigned URL TTL (1 hour, matches "short-lived" claim), and JWT algorithm handling (`algorithms=[...]` passed explicitly to `jwt.decode`, so no algorithm-confusion risk). Four real findings, all fixed:
  - **The biggest one: three LLM-cost-triggering endpoints had no rate limit at all**, unlike every other path that calls a paid LLM API. `POST /cv/upload`, `POST /cv/{id}/retry`, and `POST /sections/{id}/regenerate` were all completely open — an authenticated candidate (or anyone holding a leaked/stolen token) could spam any of them to run up real DeepSeek API spend with no backend guardrail, the same class of gap Phase 11 closed for `/auth/*` but which slipped through here since these were built in earlier phases before rate limiting existed at all. Added `10/hour` via the same `slowapi.Limiter` everything else uses. `GET /sections` deliberately left unlimited — it only calls the generation LLM for section types that don't exist yet (verified by reading `GenerationService.list_sections`), so it self-limits to at most two LLM calls ever per candidate and becomes a plain read afterward.
  - **CV upload trusted client-supplied `content_type` and filename extension alone** — neither proves what the uploaded bytes actually are, both are fully attacker-controlled. Added a magic-bytes check (`CVService._MAGIC_BYTES`) comparing the real file signature (`%PDF-`, `PK\x03\x04`, or the OLE2 header) against the claimed type, dependency-free. Verified live: a `.pdf`-labeled file containing plain text now 422s; a genuinely PDF-signed file still uploads.
  - **`AddDomainRequest.domain` accepted any 3-255 character string** with no format check — not directly exploitable today (no host-based routing is wired up yet, per Phase 14's own scope), but bad input hygiene for a field that will eventually gate real DNS/routing behavior. Added an RFC-1123 hostname regex validator; normalizes to lowercase. Verified live: `"localhost"` and `"not a domain!!"` both now 422, `"Www.Example.com"` normalizes to `"www.example.com"` and succeeds.
  - **This doc's own Security & Privacy section (§10) asserted something the code doesn't do**: it claimed the refresh token is "stored httpOnly-cookie-side on web clients," but the backend has never set a cookie — both tokens are plain JSON response body fields from day one (Phase 2), and only a `Set-Cookie` response header (not client JS) can create an httpOnly cookie. Left as a documentation fix plus an explicit recommendation rather than an API contract change: switching to cookie-based refresh is a real architectural decision (same-origin vs cross-origin frontend changes the `SameSite`/`Secure` tradeoffs) that shouldn't happen incidentally during a hardening pass. §10 now states the actual transport and the safer interim frontend pattern (access token in memory only, never `localStorage`); the Admin Panel UI reference doc follows that pattern explicitly.
  - Full quality gate re-run clean after all fixes (ruff, mypy, 83 tests — one new test added for the magic-bytes rejection), and every fix verified live against the real containers, not just the test suite.

- **2026-08-21** — Phase 14 (Phase-2 stubs, flagged off) built — the last item on the original build-phase list. §15 of this doc described these as "already accounted for in schema/architecture" since the plan was first written, but neither actually existed yet; this phase makes that true.
  - **`PublicDomain`** (new model + migration `5a035f6b6bf2`) — belongs to a `PublicChatfolio` (1:1), not directly to a `CandidateProfile`, since the thing a custom domain addresses is the published page. Holds a `verification_token` field for a future DNS TXT-record challenge; no verification logic exists yet, only somewhere to put the value once it does — same "schema now, logic later" shape as everything else in this phase.
  - **`DomainService` + three endpoints** under `/api/v1/portfolio-settings/domain` (`GET`/`POST`/`DELETE`) — every method 404s while `features.enable_custom_domains` is off (the default), same as any other not-yet-shipped surface, rather than exposing a half-working feature behind a flag check scattered across routers. Adding a new domain silently replaces any existing one for that chatfolio (one domain per chatfolio, no explicit "update" endpoint needed). Domain uniqueness enforced across all candidates (409 on conflict) — verified live with two separate candidates.
  - **`plan` + `usage_limits`** added to `CandidateProfile` (`plan: str = "free"`, `usage_limits: dict[str, int] = {}`) — no enforcement anywhere, no `enable_billing` check needed since there's nothing gated yet, just the columns. Deliberately not exposed on `ProfileResponse` — a stub field with no consumer doesn't need an API surface yet either.
  - Verified fully live: domain endpoints 404 by default; flipped `FEATURE_ENABLE_CUSTOM_DOMAINS=true` in `.env`, recreated the container, ran the full add/get/conflict/delete/re-404 cycle successfully, then flipped it back off and recreated again to confirm the endpoints return to 404 — the flag genuinely gates behavior end-to-end, not just in tests.
  - **Hit the same `create_all()` schema-evolution pitfall documented in §11**, this time from adding a column to an *existing* table with existing test rows rather than changing one: `candidate_profiles.plan`/`usage_limits` didn't exist in the already-created test database, so every test touching a profile failed with `UndefinedColumnError` until the stale test tables were dropped so `create_all()` could rebuild them. Also had to add explicit `server_default` values to the autogenerated migration's `ADD COLUMN` statements by hand — Alembic's autogenerate only picks up the Python-side `default=`, not a server-side one, and a `NOT NULL` column with no server default fails outright against a table that already has rows (`candidate_profiles` did, from real dev usage across the last several phases).

- **2026-08-21** — Phase 13 (Hardening) built: Sentry, Prometheus, a security pass, and a real load test against the live chat path.
  - **Sentry** (`config/observability.py::configure_sentry`) — opt-in via `OBSERVABILITY_SENTRY_DSN`; unset (the default everywhere but a real deployment) means `sentry_sdk.init()` is never called at all, so local/test/CI have zero network calls or behavior change. `traces_sample_rate` configurable, defaults to `0.0` (errors only, no perf tracing, until someone deliberately turns it up).
  - **Prometheus** (`configure_metrics`) — `prometheus-fastapi-instrumentator` wired up in `create_app()`, exposing `/metrics` (excluded from the OpenAPI schema — it's a scrape target, not a documented API). Verified live: per-route `http_requests_total` and `http_request_duration_*` histograms populate correctly after real traffic. Gated by `OBSERVABILITY_METRICS_ENABLED` (default on) rather than always-on, matching the opt-out-not-mandatory shape of every other cross-cutting concern in this config layer.
  - **Security headers** (`core/security_headers.py::SecurityHeadersMiddleware`) — `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security` added to every response, including error responses (verified via a 405). No CSP — this is a pure JSON API with no HTML responses of its own; the frontend serving actual pages owns that header.
  - **Dependency audit** (`pip-audit`) — two findings, both reviewed and accepted rather than blindly bumped: `pip` 26.1.2 has a CVE that only matters for `pip download --only-binary` against a malicious package index, not applicable to this project's install path. `chromadb==1.4.1` has a pre-auth RCE CVE gated behind `trust_remote_code=True` on collection creation — grepped the codebase to confirm nothing here ever sets that flag (the embedding function is always the fixed local ONNX default, never a user-supplied model repo), so the vulnerable code path is unreachable as this app uses the library. `chromadb` stays pinned to `1.4.1` regardless, per the existing pyproject.toml comment about client/server wire-protocol compatibility with the pinned Chroma server image — bumping it is a separate, deliberate decision, not something to do incidentally during a hardening pass.
  - **Load test** (`scripts/load_test_chat.py`, new, dependency-free — asyncio + httpx, no locust/k6 for a single-endpoint check) — concurrent sessions against `POST /api/v1/public/chat/sessions/{id}/messages`, the one endpoint that chains a DB write + LLM call + vector-store query per request. Run live against the real containers with a real DeepSeek key: **p50/p95 latency ~4.3s**, entirely LLM round-trip time (matches the per-task timeout tuning from the 2026-08-21 DeepSeek-timeout fix earlier in this log). More interesting than the raw number: the two existing abuse guards both fired correctly under concurrent load without being specifically targeted — sending 2 messages per session back-to-back tripped the 2-second session cooldown (Phase 9) on the second message every time, and running two batches of session-starts inside one minute tripped the 10/min per-IP `slowapi` limit (Phase 11) on the excess sessions. Both are working as designed, not bugs the load test found — but confirms the guardrails hold up under actual concurrency, not just sequential test-suite calls.
  - New dependencies (`sentry-sdk`, `prometheus-fastapi-instrumentator`) needed an image rebuild, not just a container restart — `pip install -e .` inside the running container only resolves *dependencies already installed at build time*; adding a new dependency to `pyproject.toml` requires `docker compose build` before a restart can pick it up (unlike a plain source change, which the bind mount + editable install already handles live).

- **2026-08-21** — Phase 12 (Admin APIs) built. New `AdminAuditLog` model/migration (`251cd2b9ba23`) — one immutable row per admin mutation, written by `AdminService._log()`. `AdminService` deliberately bypasses the owner-scoped `ProfileRepository`/`ProfileService` helpers every other service uses — an admin is by definition not scoped to one candidate's profile, so its queries join `User`/`CandidateProfile`/`PublicChatfolio`/`UploadedCV` directly. Six endpoints under `/api/v1/admin`, all gated by the existing `AdminUserDep` (`require_admin`, unchanged from Phase 2): `GET /users`, `GET /chatfolios` (optional `is_published` filter), `GET /metrics` (user/candidate/published-chatfolio/chat-session/chat-message/flagged-session/CV-parse-success/CV-parse-failure counts — all real aggregate queries, not cached), `GET /cv-jobs/failed`, `POST /cv-jobs/{id}/retry`, `POST /chatfolios/{id}/unpublish`. The two mutation endpoints resolve and return the owner's email alongside the mutated resource (one extra join) rather than leaving it blank in the response, and both write an audit row before returning.
  - Verified live against the real containers end-to-end, not just the test suite: registered a candidate and an admin, confirmed the candidate gets 403 on `/admin/users`; promoted the admin via direct SQL (`role='ADMIN'` — SQLAlchemy's `Enum` stores the Python member *name*, not `.value`, catching this out during manual testing since the model uses lowercase string values everywhere else); published a real chatfolio, confirmed `GET /public/chatfolio/{slug}` returns 200, had the admin find and unpublish it via `/admin/chatfolios`, confirmed the public endpoint now 404s and an `admin_audit_logs` row exists with `action='chatfolio.unpublish'`; manually inserted a `FAILED` `UploadedCV` row, confirmed it surfaces in `/admin/cv-jobs/failed`, retried it via the admin endpoint, confirmed the worker picked up the re-enqueued `parse_cv_job` from the live logs.
  - Note for whoever adds the next Alembic migration: `alembic/` is `COPY`'d into the image at build time, not bind-mounted (unlike `src/`/`scripts/`) — `alembic revision --autogenerate` run via `docker compose exec api` generates the file *inside the container only*; it must be copied back to the host manually (`docker compose exec api cat alembic/versions/<file> ` piped to a local write) or the migration is lost on the next `docker compose build`.

- **2026-08-21** — Phase 11 (Rate Limiting & Abuse Signals) closed out. Session-level cooldown and the `is_flagged` abuse signal were already built in Phase 9 (`ChatService._enforce_cooldown`), and `slowapi` was already wired up for the public chat endpoints — but §6.10 of this plan explicitly scopes rate limiting to "chat send **and auth endpoints**", and auth had none: `register`/`login`/`refresh`/`logout` were completely unlimited, meaning credential stuffing and account-creation spam had no backend guardrail at all (only whatever a reverse proxy might add, which isn't part of this codebase). Added `@limiter.limit(...)` to all four, same Redis-backed `slowapi.Limiter` the chat endpoints already use: `register` 5/min (spam-account creation, plus it's the only one that writes a new row), `login` 10/min (the actual brute-force target — deliberately looser than register since a real user mistyping a password a few times shouldn't get blocked), `refresh`/`logout` 20/min (lower abuse value since both require an already-issued token, but still bounded to prevent resource exhaustion). Verified live against the real containers, not just the test suite: 5 registrations succeed then a 6th 429s; 10 failed logins succeed (as 401s) then an 11th 429s.

- **2026-08-21** — Phase 10 (Recruiter Context Capture) built. The extraction pass and `RecruiterMetadata` persistence were already in place from Phase 9 (`RAGService.classify_and_extract`, `ChatService._merge_recruiter_metadata`); what remained was the candidate-facing side — `DashboardService` plus `GET /api/v1/dashboard/conversations`, `GET /api/v1/dashboard/conversations/{id}`, `POST /api/v1/dashboard/conversations/{id}/mark-reviewed`. List returns a lightweight summary (message count via one grouped-count query across all listed session ids, not N+1; `RecruiterMetadata` eager-loaded via `selectinload`) — detail eager-loads the full message history the same way. Every query is scoped through the caller's own `PublicChatfolio` (resolved via `PortfolioService.get_or_create_for_user`, the same helper `portfolio_settings` already uses), so a candidate can never see another candidate's conversations, even by guessing a session id directly — verified with a dedicated cross-candidate isolation test and live against the real containers. `reviewed_by_candidate` (a `ChatSession` column that already existed since Phase 9 but had no API surface) is now settable.
  - **Found and fixed while writing the tests:** `tests/integration/test_chat.py`'s `_clear_vector_store()` helper *popped* the `get_vector_store` dependency override instead of restoring conftest's default `FakeVectorStore()`. Harmless within that file alone (every test there re-sets its own override before use), but the override loss is permanent for the rest of the pytest session — any later test in *any* file that never sets its own override (as this phase's dashboard tests initially didn't, reasonably assuming the documented conftest-level default still applied) falls through to the real `get_vector_store` dependency and hits an actual Chroma server, which then 400s on the fixed 3-dimension fake embedding conftest uses (`Collection expecting embedding with dimension of 384, got 3`). Only surfaced now because this is the first new test file added after Phase 9's chat tests in collection order. Fixed by restoring the default fake instead of popping.

- **2026-08-21** — Diagnosed and fixed the recruiter-chat guardrail firing on almost every real question, reported live: "which golang projects did you do?" got the canned fallback (arguably correct — no dedicated Go project exists), but so did every unrelated follow-up in the same session ("what languages do you know?", "tell me about your current employer"), which is wrong. Two independent bugs compounded:
  - **Chroma collection used the wrong distance metric.** `ChromaVectorStore._get_collection()` called `get_or_create_collection(name=...)` with no `metadata`, so Chroma defaulted the collection to squared-L2 distance — unbounded above 1 — while `RAGService.retrieve()` computes `similarity = 1 - distance` assuming *cosine* distance (bounded [0, 2]). Verified directly: querying live embeddings returned similarity scores like `-0.010` and `-0.437` — mathematically impossible for real cosine similarity, confirming the metric mismatch rather than a data problem. Manually computed the true cosine similarity for the same vectors (`0.495`) and matched it to `1 - squared_L2/2`, confirming the formula. Fixed by creating collections with `metadata={"hnsw:space": "cosine"}`; the two existing malformed collections (`chatfolio_content`, the stale unused `profile_embeddings`) were deleted and `scripts/backfill_embeddings.py --force` re-populated the correct one against real profile data.
  - **`retrieval_similarity_threshold` (0.55) was too strict for how this embedding model actually scores.** Even after fixing the metric, real cosine similarity between a natural recruiter question and a terse resume-style chunk (`"Skill: PHP, category: Language, proficiency: Best"`) empirically lands around 0.26–0.5, only reaching 0.7+ on near-exact keyword overlap — while genuinely unrelated questions ("what's your favorite pizza topping") scored 0.07–0.16 against the same data. 0.55 was rejecting nearly all real matches. Lowered to `0.25`, which sits with a clear margin above the unrelated-question ceiling (~0.16) and below the genuine-match floor (~0.26) observed on live data. `LLMSettings.retrieval_similarity_threshold` and both `.env`/`.env.example` updated together (they'd drifted before — `.env` overrides the Python default via `LLM_RETRIEVAL_SIMILARITY_THRESHOLD`, so both must move in lockstep or a container restart silently keeps the old value).
  - **Verified end-to-end against the live containers**, not just unit-level: "What programming languages do you know?" now returns a grounded, specific answer; "Which golang projects have you built?" now gives a nuanced, honest answer (mentions the Go skill, explains no dedicated project exists, offers to elaborate) instead of the flat canned fallback — which is what the user was actually asking for ("positively negative" rather than a robotic identical string); the pizza-topping control question still correctly declines without fabricating a preference. Full quality gate (ruff, mypy, 59 tests) re-run clean afterward.
  - **Side fix, found while iterating on this:** `docker compose restart <service>` does *not* reload `env_file` — confirmed by `printenv` inside the container still showing the old threshold after a restart following the `.env` edit. Only `docker compose up -d <service>` (which recreates the container) picks up `.env` changes; documented inline for future config changes. Recreating also wipes the container filesystem, so the ONNX model cache (`/root/.cache/chroma`) was getting re-downloaded on every recreate — moved it to a named volume (`onnx_model_cache`, shared by `api`/`worker` in `docker-compose.yml`) so the ~80MB download happens once ever, not once per recreate.

- **2026-08-21** — Diagnosed and fixed a live user-reported bug: recruiter chat answered "I do not have that information" for a skill question even though the skills existed and were correctly returned by `GET /api/v1/profiles/me/skills`. Confirmed by inspecting `vector_embeddings` directly: only the two `portfolio_section` rows existed for the profile, zero for any Skill/Experience/Project/Education — they'd all been created while the containers were still running the stale pre-editable-install code from earlier the same day, so the embedding-trigger call never actually fired. Fixed in three parts:
  - **`scripts/backfill_embeddings.py`** (new, reusable, not a one-off): enqueues `embed_content_job` for every embeddable row missing a `VectorEmbedding` pointer (or all of them, with `--force`), for one profile or every profile. Safe to re-run anytime. Moved the four chunk-text builder functions (`experience_chunk_text` etc.) out of `api/api/v1/profiles.py` into `services/embedding_service.py` as `EMBEDDABLE_CHILD_TYPES` — a router module was the wrong home for logic a maintenance script also needs, and this is now the single source of truth both places import from. Added `scripts/` to the `Dockerfile` `COPY` list and to `docker-compose.yml`'s bind mounts (api/worker) so it ships with the image and stays live-editable like `src/`. Ran it against the affected profile: 12 jobs enqueued, all 12 confirmed landed in `vector_embeddings` after the worker processed them.
  - **Thundering-herd model download, discovered while watching that backfill run.** `vectorstore/local_embedder.py`'s embedding function downloads its ~80MB ONNX model lazily on *first call*, with no locking around the check-and-fetch. arq runs jobs concurrently, so a burst of 12 `embed_content_job` calls all hitting "first call" simultaneously each spawned their own thread independently racing to download the same file — visible directly as a dozen simultaneous, competing progress bars in the worker log, several times slower in aggregate than one clean download. Fixed by pre-warming the model serially in `workers/worker.py`'s `on_startup`, before the worker accepts any job — one `embed_texts(["warmup"])` call blocks startup until the model is cached, so every real job afterward just hits disk.
  - **The api container needed the identical fix, separately.** The live public-chat retrieval path (`RAGService.retrieve()`) also calls `embed_texts()`, from inside the `api` container — a different container, different filesystem, different cache from `worker`. Without its own pre-warm, the *first real recruiter message* would have hung on this same download instead of getting a timely reply. Added a FastAPI `lifespan` handler to `main.py` that does the identical `embed_texts(["warmup"])` pre-warm at API startup. Deliberately does not affect the test suite: `httpx.ASGITransport` never triggers lifespan events, and even if it did, `tests/conftest.py` already overrides `get_embed_texts` with a fake before any real embedder code would run — confirmed by timing (test suite still ran in ~9s after adding this).

- **2026-08-21** — Two fixes from a real run against the actual `api`/`worker` Docker containers with a live `LLM_DEEPSEEK_API_KEY` configured (the first time this project's containers, as opposed to a local venv, had been exercised end-to-end since Phase 1):
  - **The containers had been running stale code since Phase 1.** `docker-compose.yml` bind-mounts `./src` over `/app/src` for `api`/`worker`, which looks like it should give live code updates on restart — but the `Dockerfile` did a plain `uv pip install --system --no-cache .`, which *copies* the package into site-packages once at build time rather than linking it. The bind mount was therefore cosmetic: the running containers kept serving whatever existed at the last `docker compose build`, regardless of any subsequent source change or container restart, for the entire project so far. Every fix made in Phases 1–9 had only ever been verified via a local venv, never via these containers. Fixed by switching to an editable install (`-e .`), which does resolve against the live bind-mounted path — confirmed directly: `chatfolio.__file__` now resolves to `/app/src/chatfolio/__init__.py`, not the old site-packages copy. **Going forward, `docker compose restart api worker` is enough to pick up a source change — a full rebuild is only needed when `pyproject.toml`'s dependencies change.**
  - **`DeepSeekProvider` used one flat 60s timeout for every task.** Once the containers were actually running current code, a real DeepSeek response for the intent-classification call was slow, and the live chat request sat waiting the full 60 seconds before the (correctly-designed, working-as-intended) fallback to `unknown` intent even kicked in — a terrible experience for a recruiter watching a stalled chat widget, even though nothing crashed and the guardrail behavior underneath was correct. `INTENT` and `CHAT` sit back-to-back in the same live public-chat request (a slow `INTENT` call delays `CHAT` starting at all), so both now fail fast (10s / 30s); `GENERATION` (the CMS's own request, not a public one) and `EXTRACTION` (runs in the background CV-parsing job, not blocking any live response) can still afford to wait longer for a good result (45s / 90s). Timeouts are now selected per `LLMTask` in `LLMProviderFactory.for_task()` rather than hardcoded in the provider.

- **2026-08-21** — Phase 9 (Chat/RAG) built: `ChatSession`/`ChatMessage`/`RecruiterMetadata` models, `RAGService` (intent classification + retrieval + grounded generation), `ChatService` (persistence, cooldown, recruiter-context merge), `POST /api/v1/public/chat/{slug}/sessions`, `POST /api/v1/public/chat/sessions/{id}/messages`. **Guardrail design**: for fact-seeking intents (skill/project/experience/education/availability), if retrieval finds nothing above the similarity threshold, the canned fallback is returned *without ever calling the generation LLM* — the strongest possible guardrail, since a call that never happens cannot hallucinate. Conversational intents (greetings, role-fit chat, contact requests) still generate using the candidate's already-public-approved intro/summary as a safe baseline even with zero retrieval hits, and an "ungrounded response" warning is logged (not blocked) for admin review per the requirement's guardrail spec. Intent classification and recruiter-context extraction are combined into one LLM call (one JSON response carries both), not two, to halve the per-message LLM cost. Recruiter context merge is first-mention-wins — a later, possibly offhand remark never overwrites context already captured earlier in the same conversation. `slowapi` (previously an unused dependency since Phase 1) is now actually wired up: 10/min on session-start, 15/min on send-message, backed by the same Redis the arq queue uses. A second, app-level cooldown (2s between messages, independent of the per-IP slowapi limit) tracks `rapid_fire_count` on the session itself and flags it for admin review after repeated violations — this is the "session-level cooldown" and "abuse flag" from the requirement, distinct from slowapi's blunter per-IP throttle. Ninth migration applied (`2a0b6bc7536e`).
  - **Requested bug/bad-practice scan across all prior phases turned up two real issues, fixed alongside this phase**: (1) `public_portfolio.py` used a bare `assert profile is not None` for a runtime invariant — `assert` is stripped entirely under `python -O`, which would have crashed on `.full_name` with a confusing `AttributeError` instead of a clean error if the (should-be-impossible) case were ever hit; replaced with an explicit `ServiceUnavailableError` raise. (2) The default `SECURITY_JWT_SECRET` was only 16 bytes — short enough to trigger PyJWT's `InsecureKeyLengthWarning` on literally every request in every test run throughout this project so far; lengthened to 32+ bytes, and `create_app()` now refuses to start at all with that default value outside a `local` environment, so the placeholder can never accidentally reach a real deployment. The warning persisted through a first verification pass after this change — `.env` and `.env.example` both hardcode the *literal* old short value, which as an env var takes precedence over the new (longer) Python-level default; both files needed updating too, not just `settings.py`. A reminder that "change the default" and "change every place that shadows the default" are two different steps.
  - **Building this phase surfaced three more real bugs, none caught by the automated suite** (all three only appeared under real Postgres/Redis/Chroma, which is exactly why every phase in this project has included a live E2E pass, not just `pytest`): (1) A cooldown false-positive on a session's *first* message — `last_active_at` was set to "now" at session creation, so a recruiter sending their first message within 2 seconds of starting a session (completely normal) got rejected as "too fast" against the session-start timestamp rather than against a previous message. Fixed by making `last_active_at` nullable and only enforcing cooldown once it's non-null. (2) The rapid-fire counter increment had the exact same "rolled back on the exception that reports it" shape as the DeepSeek-key bug from Phase 6 — raising `TooManyRequestsError` unwound to `get_db_session`'s except-rollback before the increment was ever committed, silently discarding every single violation. Fixed the same way: commit explicitly before raising, not after. (3) `get_vector_store()` constructed a brand-new `ChromaVectorStore` (and therefore a brand-new `chromadb.AsyncHttpClient`) on *every single request* — the only dependency in the whole codebase not following the lazy-singleton pattern already established for the DB engine and the arq pool. Under real back-to-back requests this intermittently broke against the live Chroma server in a way a serial test script never reproduced; fixed by caching one instance for the process lifetime, matching every other shared resource.
  - **The actual root cause under two of those "intermittent Chroma failures" turned out to be a fourth, unrelated bug**, found only by refusing to accept "it's flaky" as an explanation: `.env`'s `VECTORSTORE_PORT` had been left at `8001` (chromadb's upstream default) since before Phase 1, when `docker-compose.yml`'s `chroma` service was remapped to host port `8004` to dodge a local conflict — nobody had gone back to update the corresponding app setting. Port 8001 wasn't idle; it happened to be bound by a *completely unrelated project's own Chroma container* also running on this machine (`banglalink_chromadb`), so every real request from this app had been silently talking to a different project's vector database the entire time, on a possibly-incompatible server version, explaining the version-mismatch-shaped errors. Found via `docker ps` after direct-script Chroma calls kept succeeding while identical calls through the app kept failing — the discrepancy between "hardcoded-correct-port script" and "reads-.env app" was the tell. Fixed `.env` and `.env.example`, and added a comment on the setting warning that a host port collision with some *other* project is exactly the kind of thing that causes this silently. The `get_vector_store()` singleton fix above is still correct and stays regardless — spawning one HTTP client per request was real waste independent of which port it was pointed at.

- **2026-08-20** — Phase 8 (Publish & Public API) built: `PublicChatfolio` model, `PortfolioService` (owner-facing settings/publish, authed) + `PublicPortfolioService` (public resolution, no auth) in one `portfolio_service.py` per the original file plan, `GET/PATCH /api/v1/portfolio-settings` + `POST .../publish` + `POST .../unpublish`, `GET /api/v1/public/chatfolio/{slug}` + `GET /api/v1/public/chatfolio/{slug}/cv`. **Publish gating is the concrete enactment of the core "no publish without review" principle**: `publish()` requires both `intro` and `summary` `PortfolioSection`s to be `status=approved` (Phase 6's workflow) plus a full name set, or it 422s listing exactly what's missing — there's no separate "approve profile" endpoint; publish IS the approval gate, and it also flips `CandidateProfile.status` to `approved` as a side effect. Experience/Project/Skill/Education need no such gate on the public page — consistent with Phase 7's reasoning, they're the candidate's own directly-entered facts, not AI output needing review. **Subdomain is computed, not stored** (`subdomain_for(slug)` = `"{slug}.chatfolio.com"`) — a second persisted column that has to be kept in sync with `slug` on every rename is a drift bug waiting to happen for a value the requirement never asks to diverge from slug. **Old-slug redirects**: `previous_slug` is set whenever `slug` changes (single previous value, not a full history — sufficient for "the last rename," which is what the requirement asks for); the public GET checks it and issues a 307 rather than 404ing a link someone already has. `PublicDomain` (the Phase-2 custom-domain stub from the original data model) was deliberately not built this phase — out of scope until custom domains are actually prioritized, not an oversight. Eighth migration applied (`8ef99f864695`). Verified end-to-end against the real dev database (not just the automated suite): registered, filled profile + experience, approved both sections, renamed slug, published, fetched the live public page, unpublished and confirmed it 404s again — full loop, no gaps. 49/49 automated tests pass, including that draft/unapproved content is never exposed publicly (edited-after-approval section content disappears from the public page immediately) and that the redirect chain actually returns `307` with the correct `Location` header.

- **2026-08-20** — Phase 7 (Vector Embeddings) built: `VectorStore` protocol + `ChromaVectorStore` adapter, `VectorEmbedding` pointer model, `EmbeddingService`, `embed_content_job`. **Embedding provider decision**: DeepSeek has no embeddings API and no other provider is configured, so embeddings use Chroma's bundled local ONNX model (`vectorstore/local_embedder.py`, all-MiniLM-L6-v2) instead of routing through `LLMProviderFactory` — no API key, no per-call cost, fully swappable later behind the same `VectorStore` interface (see §7). **Trigger design, two different policies by design, not oversight**: Experience/Project/Skill/Education are the candidate's own directly-entered facts, so create/update re-embeds immediately — there's no hallucination risk to review, unlike AI-authored content. Approved `PortfolioSection` content is the opposite: it's LLM-generated prose, so it only embeds on explicit `approve()`, and editing or regenerating an already-approved section immediately deletes its stale embedding (via `EmbeddingService.delete_embed`) rather than leaving outdated AI text available to ground recruiter chat until the candidate re-approves. Deletion is synchronous (cheap, no embedding computation); creation/update goes through `embed_content_job` (expensive — CPU-bound local inference) via the same `ctx`-injection pattern as `parse_cv_job`, run through `asyncio.to_thread` so it doesn't block the worker's event loop. `chroma_ref_id = "{source_type}:{source_id}"` makes re-embedding a plain Chroma `upsert`-in-place rather than needing separate delete-then-create logic. Seventh migration applied (`41ea3eb36748`). Skills are embedded one row at a time (not aggregated into a single "skills summary" chunk as originally considered) — deliberately simpler for the pilot; revisit only if retrieval quality on skill questions proves this wrong.
  - **Two real bugs found and fixed during verification.** (1) A test-fixture cleanup bug in `test_embedding_triggers.py`: its `spy_vector_store`/`spy_job_queue` fixtures `.pop()`ped their dependency overrides on teardown instead of restoring conftest's global fake default — popping removes the *global* default too, so every test that ran afterward in the same session silently fell through to a real Chroma connection. Symptom was three unrelated tests in other files failing, but only when the full suite ran (never in isolation) — the tell that it's an ordering/global-state bug, not a logic bug. Fixed by having those fixtures restore a fresh fake on teardown, never pop to nothing; documented as a standing rule in §11.
  - (2) That real Chroma connection then failed with a wire-protocol error (`KeyError('_type')`) — traced to `pyproject.toml`'s floating `chromadb>=0.5` resolving to client `1.5.9`, while `docker-compose.yml`'s `chromadb/chroma:latest` image was actually server `1.4.4` (confirmed the *only* `latest` tag exists — Chroma publishes no per-version stable tags for this image, only `latest` and internal dev builds). Pinned the client to `chromadb==1.4.1` (the closest published PyPI release, verified compatible against the live server directly) and pinned the Docker image **by digest** rather than tag, since tag pinning wasn't actually possible here — `docker-compose.yml` now points at the exact digest that resolves to server `1.4.4`. Both pins carry a comment pointing at each other so a future version bump isn't done on just one side.
- **2026-08-20** — Phase 6 (AI Generation & Review) built: `PortfolioSection` model, `GenerationService`, `GET/PATCH /api/v1/sections`, `POST /api/v1/sections/{id}/regenerate`, `POST /api/v1/sections/{id}/approve`. **Scope decision**: narrowed `PortfolioSection.section_type` from the original plan's 6 values down to just `intro` and `summary` — experience/projects/skills/education are already fully-modeled CRUD tables from Phase 3, and a separate narrative "section" for those would just duplicate that data; those four render directly from their own tables on the public page (Phase 8), while intro/summary are the two that genuinely need LLM-authored prose. `GET /api/v1/sections` lazily generates any missing section on first call (same get-or-create pattern as `CandidateProfile` in Phase 3) rather than needing a separate create endpoint. Context for generation is built from whatever the candidate has actually entered (`CandidateProfile` + Experience/Project/Skill/Education), falling back to the latest parsed CV's raw text only when those tables are still empty — so generation works immediately after CV upload, before the candidate has manually populated the structured tables. Editing or regenerating an approved section resets it to `draft`, preserving the "no publish without re-review" guarantee. `CVResponse` now also exposes `parsed_json` so the CMS can show extracted data as reference alongside the editable structured forms. `LLMFactory` protocol added to `llm/base.py` so services/tests depend on "something that resolves a provider per task," not the concrete `LLMProviderFactory` — this is what makes `GET /api/v1/sections` testable via `app.dependency_overrides` with a fake factory, no network calls needed. Sixth migration applied (`9d522e6ed93c`). During live verification against the dev stack (no `LLM_DEEPSEEK_API_KEY` configured), found and fixed two real issues: (1) `GenerationService` let LLM-call failures propagate as raw unhandled exceptions on this synchronous HTTP path — added `ServiceUnavailableError` (503) as a clean boundary around the LLM call, unlike Phase 5's CV job this can't just mark a row `FAILED` and move on, the request needs a real-time response; (2) that same live check surfaced a sharper bug underneath: `.env`'s `LLM_DEEPSEEK_API_KEY=` (empty, not unset) loads as `SecretStr("")` rather than `None`, so `LLMProviderFactory`'s `is None` check missed it and a real DeepSeek call went out with a blank Bearer token, failing with a confusing low-level protocol error instead of a clear "not configured" message — fixed by treating an empty secret the same as an unset one.
- **2026-08-19** — Phase 5 (CV Parsing Pipeline) built: `cv_parsing` (pymupdf for PDF, python-docx for DOCX; legacy `.doc` deliberately fails fast with a clear message — python-docx cannot parse the binary format), `LLMProvider` protocol + `LLMProviderFactory` (only `DeepSeekProvider` implemented, per the requirement's default — GPT/Gemini/Claude/Grok/OpenRouter stay unimplemented branches until a task actually needs one, not speculative stub classes), and `workers/jobs_cv.py`'s `parse_cv_job`. Resources (`sessionmaker`/`storage`/`llm_factory`) are injected into the job via arq's `ctx` dict at worker startup rather than constructed inside the job function — this is what makes the job a plain function tests can call directly with a hand-built `ctx` (fake storage, fake LLM factory), with no real arq worker or network call needed for the test suite. Enqueueing is behind a `JobQueue` protocol (structurally satisfied by arq's real pool, no inheritance needed) injected into `CVService`, which now enqueues `parse_cv_job` on both upload and retry. Verified by actually running the arq worker against the dev stack (not just the test suite): a garbage PDF fails cleanly at extraction, a real PDF gets through extraction and fails cleanly at the LLM call (no `LLM_DEEPSEEK_API_KEY` configured yet) — in both cases status lands on `FAILED` with a clear message, never stuck on `PROCESSING`. Fifth migration was not needed (no schema change this phase). While doing that live-worker verification, found and fixed a third instance of the dev/test resource-bleed pattern — see the Redis entry below — plus confirmed `RedisSettings.db` had never actually been wired into arq's connection settings since Phase 1.
- **2026-08-19** — Phase 4 (Storage & CV Upload) built: `StorageBackend` protocol + `S3StorageBackend` (aioboto3, works against both MinIO locally and AWS S3 in prod, lazily creates the bucket on first upload), `UploadedCV` model — reusing `ProfileChildMixin` since a CV is just another CandidateProfile-owned aggregate, so no new repository class was needed (`CVService` is built entirely on the existing `ProfileService` generic child methods). `POST /api/v1/cv/upload` validates content-type with an extension fallback (some clients send `application/octet-stream` for `.docx`) and enforces the 20MB cap from `Requirement.md`; `GET /api/v1/cv/{id}/status` and `POST /api/v1/cv/{id}/retry` (retry only allowed from `failed` status). Verified with real uploads against a live MinIO container. Fourth migration applied (`60adad69c93e`). While verifying this phase, discovered CV upload tests were polluting the dev MinIO bucket (see the storage-isolation entry below) and fixed it in the same pass.
- **2026-08-19** — Fixed a data-loss bug: the automated test suite was truncating every table in the same database the developer's locally running app/`.env` pointed at, so any `pytest` run silently deleted whatever account had been registered through the real app. Tests now force `DATABASE_NAME=chatfolio_test` (see §11) with an `assert "test" in ...` guard as a second line of defense; `chatfolio_test` is provisioned via `docker/postgres/init-test-db.sql` locally and via the CI Postgres service's `POSTGRES_DB`. Also stopped deleting `.env` during verification cleanup — it's the developer's persistent local config, not a build artifact, and should never be touched by anything other than the developer.
- **2026-08-19** — Fixed a Swagger-doc bug in Phase 3's profile routes: `APIRouter(prefix="/profiles/me", tags=["profiles"])` gave every route a router-level tag, and the child routes *also* passed their own `tags=[tag]` — two tags means FastAPI/Swagger lists the operation once per tag, so every experience/project/skill/education route appeared twice in the docs (once under "profiles", once under its own section). Fix: don't set a blanket tag on the router; tag the two `/me` profile routes explicitly (`tags=["profile"]`) and leave child routes with only their own resource tag. **Rule for future routers**: a route must end up with exactly one tag — either set it once at the router level and never override per-route, or never set it at the router level and always set it per-route/per-factory-call. Mixing both is what caused this.
- **2026-08-19** — Phase 3 (Candidate Profile Core) built: `CandidateProfile` + `Experience`/`Project`/`Skill`/`Education` models sharing a `ProfileChildMixin` (id + `profile_id` FK), `GET/PATCH /api/v1/profiles/me`, and a `register_child_routes()` factory that generates list/create/update/delete for all four child resources instead of four near-duplicate route modules (DRY). Profile is lazily get-or-created on first access rather than requiring a separate create step. Ownership is enforced entirely from `current_user` — no `profile_id`/`owner_id` is ever accepted from the client — verified by a test that a second user gets 404 on another user's experience row. Removed a `slug` field that had been duplicated on both `CandidateProfile` and `PublicChatfolio` in §5 — it belongs only on `PublicChatfolio` (Phase 7); fixed here before it became a real migration. Second migration applied (`d002d49b5e0b`).
- **2026-08-19** — Phase 2 (Auth & User Management) built: `User`/`RefreshToken` models, argon2 hashing, JWT access + rotated opaque refresh tokens, `require_owner`/`require_admin`-style guard dependencies (`get_current_user`, `require_admin`), `/api/v1/auth/*` endpoints, first Alembic migration. Added `RefreshToken` table (§5) and the test-isolation pattern (§11) not anticipated in the original plan.
- **2026-08-19** — Initial backend plan created from `Requirement.md`.
