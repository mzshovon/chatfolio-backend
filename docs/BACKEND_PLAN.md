# Chatfolio Backend — Engineering Knowledge Base

> Living document. Update this file whenever an architectural decision, module, or scope changes.
> Do not let code drift from this doc — if you build something differently than described here, edit this file in the same PR.
> See [Changelog](#16-changelog) at the bottom for update rules.

Source of truth for functional scope: `Requirement.md` (repo root). This document is the *how*; `Requirement.md` is the *what*.

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
│       ├── schemas/                # Pydantic request/response DTOs, mirrors api/v1 modules
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
| `PublicChatfolio` | profile_id (FK, unique), slug (unique), subdomain (unique), is_published, published_at, contact_cta_config (JSONB), cv_downloadable (bool, default true per requirement) | 1:1 CandidateProfile; 1:N PublicDomain, ChatSession |
| `PublicDomain` *(Phase-2 stub, table exists, endpoints flagged off)* | chatfolio_id (FK), domain, verification_status, ssl_status | belongs to PublicChatfolio |
| `VectorEmbedding` | profile_id (FK), source_type, source_id, chunk_text, chroma_ref_id, metadata (JSONB) | belongs to CandidateProfile; mirrors what's stored in Chroma for reindex/debug |
| `ChatSession` | chatfolio_id (FK), recruiter_session_token, started_at, last_active_at | belongs to PublicChatfolio; 1:N ChatMessage; 1:1 RecruiterMetadata |
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
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

class LLMProviderFactory:
    def for_task(self, task: LLMTask) -> LLMProvider:
        provider_name = self._settings.llm.provider_for(task)  # falls back to default_provider
        return self._registry[provider_name]
```

- One `.providers.<name>` module per vendor, each implementing the same `LLMProvider` protocol against that vendor's SDK/HTTP API.
- Default provider: DeepSeek (per requirement). GPT/Gemini/Claude/Grok/OpenRouter are registered but only instantiated (API client created) if their key is configured — no wasted setup cost.
- Per-task override means, e.g., extraction could later run on a cheaper/faster model than chat without touching service code.
- Embedding model is part of this same abstraction (`embed()`), so swapping the embedding provider is also a config change.

---

## 8. Background Jobs (arq)

| Job | Trigger | Idempotency key |
|---|---|---|
| `parse_cv_job` | CV upload / retry | `(cv_id, file_hash)` |
| `generate_sections_job` | after parse success, or manual "generate" action | `(profile_id, section_type, source_version)` |
| `embed_content_job` | section/profile approval | `(profile_id, section_id, content_hash)` |

Worker process runs as a separate container (`workers/worker.py`), sharing the same `models`/`services`/`llm` code as the API — no duplicated logic between request path and job path.

---

## 9. API Surface Map (v1)

```
POST   /v1/auth/register
POST   /v1/auth/login
POST   /v1/auth/refresh
POST   /v1/auth/logout

GET    /v1/profiles/me
PATCH  /v1/profiles/me
POST   /v1/profiles/me/experience        (+ GET/PATCH/DELETE /{id})
POST   /v1/profiles/me/projects          (+ GET/PATCH/DELETE /{id})
POST   /v1/profiles/me/skills            (+ GET/PATCH/DELETE /{id})
POST   /v1/profiles/me/education         (+ GET/PATCH/DELETE /{id})

POST   /v1/cv/upload
GET    /v1/cv/{id}/status
POST   /v1/cv/{id}/retry

GET    /v1/sections                       # list draft/approved sections
PATCH  /v1/sections/{id}                   # edit content
POST   /v1/sections/{id}/regenerate
POST   /v1/sections/{id}/approve

GET    /v1/portfolio-settings
PATCH  /v1/portfolio-settings              # slug, cta, cv visibility, theme
POST   /v1/portfolio-settings/publish
POST   /v1/portfolio-settings/unpublish

GET    /v1/public/chatfolio/{slug}         # no auth; resolves slug or subdomain
GET    /v1/public/chatfolio/{slug}/cv      # download, if enabled

POST   /v1/public/chat/{slug}/sessions
POST   /v1/public/chat/sessions/{session_id}/messages
GET    /v1/dashboard/conversations          # candidate-owned, authed
GET    /v1/dashboard/conversations/{id}
POST   /v1/dashboard/conversations/{id}/mark-reviewed

GET    /v1/admin/users
GET    /v1/admin/chatfolios
GET    /v1/admin/metrics
GET    /v1/admin/cv-jobs/failed
POST   /v1/admin/cv-jobs/{id}/retry
POST   /v1/admin/chatfolios/{id}/unpublish
```

---

## 10. Security & Privacy

- Argon2 password hashing; JWT access token short-lived (~15 min), refresh token longer-lived and rotated on use, refresh stored httpOnly-cookie-side on web clients.
- CORS restricted to known frontend origins per environment.
- File upload: type allow-list + 20MB size cap enforced before write, not after.
- CV downloads and file storage: private bucket, served via short-lived presigned URLs — never a public-by-default bucket, even though the *download feature* defaults to publicly visible per requirement (the bucket stays private; visibility is an application-level gate, not an S3 ACL).
- Ownership checks on every candidate-scoped mutation (`require_owner`).
- Public endpoints never read from unapproved/draft tables.
- Chat logs restricted to owning candidate + admin (enforced at repository query level: always filtered by profile ownership).
- Admin actions logged (`AdminAuditLog`).
- Data export/delete: not built in MVP, but `CandidateProfile` + related tables are already scoped by a single FK chain from `user_id`, so a future "export/delete everything for this user" job is a straightforward cascading query — no schema rework needed later.

---

## 11. Testing Strategy

- **Unit** — services tested against fake repositories and a fake `LLMProvider`/`VectorStore` (no network, no DB). Fast, run on every commit.
- **Integration** — real Postgres (docker), real Redis, `httpx.AsyncClient` against the FastAPI app; LLM providers mocked at the HTTP layer (no real API spend in CI).
- **Guardrail fixtures** — a small fixed set of recruiter questions with known "not in profile" answers, asserted against the fallback template, to catch guardrail regressions.
- **Test isolation** — the DB engine is process-lifetime `lru_cache`d and bound to whatever event loop first created it, so pytest-asyncio must run fixtures and tests on one shared loop (`asyncio_default_fixture_loop_scope = "session"` / `asyncio_default_test_loop_scope = "session"` in `pyproject.toml`) — per-test loops break asyncpg connections. Data isolation between tests is a `conftest.py` autouse fixture that `TRUNCATE`s every table in `Base.metadata` after each test; it reads the table list from the ORM metadata (not a hardcoded list) so it never needs updating as new models land.
- **Tests must never point at the dev database.** `tests/conftest.py` forces `DATABASE_NAME` to `chatfolio_test` (overridable via `TEST_DATABASE_NAME`) *before* anything imports `chatfolio.main` (which builds the app, and therefore reads settings, at import time), and both the schema-setup and truncate fixtures hard-`assert "test" in settings.database.name` as a second line of defense. This is load-bearing, not decorative: early in this project's history the test suite ran against the same `chatfolio` database backing the locally running `api` container, so every `pytest` run silently deleted whatever the developer had registered through the actual app. `docker/postgres/init-test-db.sql` auto-provisions `chatfolio_test` on a fresh volume; CI's Postgres service creates it directly via `POSTGRES_DB`. If a new test database name or a second test DB is ever introduced, keep the assertion — don't relax it for convenience.
- **The same rule applies to object storage.** `tests/conftest.py` forces `STORAGE_BUCKET` to `chatfolio-cv-test` (overridable via `TEST_STORAGE_BUCKET`), asserted the same way, with a session-scoped fixture that empties the test bucket before *and* after the run. Discovered this was needed the same day as the DB issue: CV upload tests were writing real objects into the same `chatfolio-cv` bucket the dev `api` container serves from — non-destructive (nothing gets deleted), but it would have silently accumulated test files in the dev bucket forever. Any future resource a test touches (a queue, a cache namespace, a second bucket) gets the same treatment: dedicated name, asserted before touching it, cleaned up after.
- **...and to the job queue.** `tests/conftest.py` forces `REDIS_DB` to `1` (overridable via `TEST_REDIS_DB`), asserted via `settings.redis.db != 0` (0 is the dev/worker default — Redis db indices are numeric, so there's no `"test"`-substring check to reuse), with a session-scoped fixture that `FLUSHDB`s that index before and after the run. Found by actually running the real arq worker against the dev stack during Phase 5 verification: CV upload tests enqueue `parse_cv_job`, and before this fix they enqueued into the same Redis db the dev worker consumes from — a real dev worker would have picked up jobs referencing rows that only exist in `chatfolio_test`, logged `cv_not_found`, and moved on (harmless, but still cross-environment bleed, and it *was* observed happening live). Also caught in the same pass: `RedisSettings.db` existed as a config field since Phase 1 but was never actually passed to `ArqRedisSettings` in `workers/queue.py` or `workers/worker.py` — both always connected to db 0 regardless of config. Fixed alongside the test-isolation change.
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

Update this section (newest first) whenever this document changes. One line: date, what changed, why.

- **2026-08-20** — Phase 6 (AI Generation & Review) built: `PortfolioSection` model, `GenerationService`, `GET/PATCH /v1/sections`, `POST /v1/sections/{id}/regenerate`, `POST /v1/sections/{id}/approve`. **Scope decision**: narrowed `PortfolioSection.section_type` from the original plan's 6 values down to just `intro` and `summary` — experience/projects/skills/education are already fully-modeled CRUD tables from Phase 3, and a separate narrative "section" for those would just duplicate that data; those four render directly from their own tables on the public page (Phase 8), while intro/summary are the two that genuinely need LLM-authored prose. `GET /v1/sections` lazily generates any missing section on first call (same get-or-create pattern as `CandidateProfile` in Phase 3) rather than needing a separate create endpoint. Context for generation is built from whatever the candidate has actually entered (`CandidateProfile` + Experience/Project/Skill/Education), falling back to the latest parsed CV's raw text only when those tables are still empty — so generation works immediately after CV upload, before the candidate has manually populated the structured tables. Editing or regenerating an approved section resets it to `draft`, preserving the "no publish without re-review" guarantee. `CVResponse` now also exposes `parsed_json` so the CMS can show extracted data as reference alongside the editable structured forms. `LLMFactory` protocol added to `llm/base.py` so services/tests depend on "something that resolves a provider per task," not the concrete `LLMProviderFactory` — this is what makes `GET /v1/sections` testable via `app.dependency_overrides` with a fake factory, no network calls needed. Sixth migration applied (`9d522e6ed93c`). During live verification against the dev stack (no `LLM_DEEPSEEK_API_KEY` configured), found and fixed two real issues: (1) `GenerationService` let LLM-call failures propagate as raw unhandled exceptions on this synchronous HTTP path — added `ServiceUnavailableError` (503) as a clean boundary around the LLM call, unlike Phase 5's CV job this can't just mark a row `FAILED` and move on, the request needs a real-time response; (2) that same live check surfaced a sharper bug underneath: `.env`'s `LLM_DEEPSEEK_API_KEY=` (empty, not unset) loads as `SecretStr("")` rather than `None`, so `LLMProviderFactory`'s `is None` check missed it and a real DeepSeek call went out with a blank Bearer token, failing with a confusing low-level protocol error instead of a clear "not configured" message — fixed by treating an empty secret the same as an unset one.
- **2026-08-19** — Phase 5 (CV Parsing Pipeline) built: `cv_parsing` (pymupdf for PDF, python-docx for DOCX; legacy `.doc` deliberately fails fast with a clear message — python-docx cannot parse the binary format), `LLMProvider` protocol + `LLMProviderFactory` (only `DeepSeekProvider` implemented, per the requirement's default — GPT/Gemini/Claude/Grok/OpenRouter stay unimplemented branches until a task actually needs one, not speculative stub classes), and `workers/jobs_cv.py`'s `parse_cv_job`. Resources (`sessionmaker`/`storage`/`llm_factory`) are injected into the job via arq's `ctx` dict at worker startup rather than constructed inside the job function — this is what makes the job a plain function tests can call directly with a hand-built `ctx` (fake storage, fake LLM factory), with no real arq worker or network call needed for the test suite. Enqueueing is behind a `JobQueue` protocol (structurally satisfied by arq's real pool, no inheritance needed) injected into `CVService`, which now enqueues `parse_cv_job` on both upload and retry. Verified by actually running the arq worker against the dev stack (not just the test suite): a garbage PDF fails cleanly at extraction, a real PDF gets through extraction and fails cleanly at the LLM call (no `LLM_DEEPSEEK_API_KEY` configured yet) — in both cases status lands on `FAILED` with a clear message, never stuck on `PROCESSING`. Fifth migration was not needed (no schema change this phase). While doing that live-worker verification, found and fixed a third instance of the dev/test resource-bleed pattern — see the Redis entry below — plus confirmed `RedisSettings.db` had never actually been wired into arq's connection settings since Phase 1.
- **2026-08-19** — Phase 4 (Storage & CV Upload) built: `StorageBackend` protocol + `S3StorageBackend` (aioboto3, works against both MinIO locally and AWS S3 in prod, lazily creates the bucket on first upload), `UploadedCV` model — reusing `ProfileChildMixin` since a CV is just another CandidateProfile-owned aggregate, so no new repository class was needed (`CVService` is built entirely on the existing `ProfileService` generic child methods). `POST /v1/cv/upload` validates content-type with an extension fallback (some clients send `application/octet-stream` for `.docx`) and enforces the 20MB cap from `Requirement.md`; `GET /v1/cv/{id}/status` and `POST /v1/cv/{id}/retry` (retry only allowed from `failed` status). Verified with real uploads against a live MinIO container. Fourth migration applied (`60adad69c93e`). While verifying this phase, discovered CV upload tests were polluting the dev MinIO bucket (see the storage-isolation entry below) and fixed it in the same pass.
- **2026-08-19** — Fixed a data-loss bug: the automated test suite was truncating every table in the same database the developer's locally running app/`.env` pointed at, so any `pytest` run silently deleted whatever account had been registered through the real app. Tests now force `DATABASE_NAME=chatfolio_test` (see §11) with an `assert "test" in ...` guard as a second line of defense; `chatfolio_test` is provisioned via `docker/postgres/init-test-db.sql` locally and via the CI Postgres service's `POSTGRES_DB`. Also stopped deleting `.env` during verification cleanup — it's the developer's persistent local config, not a build artifact, and should never be touched by anything other than the developer.
- **2026-08-19** — Fixed a Swagger-doc bug in Phase 3's profile routes: `APIRouter(prefix="/profiles/me", tags=["profiles"])` gave every route a router-level tag, and the child routes *also* passed their own `tags=[tag]` — two tags means FastAPI/Swagger lists the operation once per tag, so every experience/project/skill/education route appeared twice in the docs (once under "profiles", once under its own section). Fix: don't set a blanket tag on the router; tag the two `/me` profile routes explicitly (`tags=["profile"]`) and leave child routes with only their own resource tag. **Rule for future routers**: a route must end up with exactly one tag — either set it once at the router level and never override per-route, or never set it at the router level and always set it per-route/per-factory-call. Mixing both is what caused this.
- **2026-08-19** — Phase 3 (Candidate Profile Core) built: `CandidateProfile` + `Experience`/`Project`/`Skill`/`Education` models sharing a `ProfileChildMixin` (id + `profile_id` FK), `GET/PATCH /v1/profiles/me`, and a `register_child_routes()` factory that generates list/create/update/delete for all four child resources instead of four near-duplicate route modules (DRY). Profile is lazily get-or-created on first access rather than requiring a separate create step. Ownership is enforced entirely from `current_user` — no `profile_id`/`owner_id` is ever accepted from the client — verified by a test that a second user gets 404 on another user's experience row. Removed a `slug` field that had been duplicated on both `CandidateProfile` and `PublicChatfolio` in §5 — it belongs only on `PublicChatfolio` (Phase 7); fixed here before it became a real migration. Second migration applied (`d002d49b5e0b`).
- **2026-08-19** — Phase 2 (Auth & User Management) built: `User`/`RefreshToken` models, argon2 hashing, JWT access + rotated opaque refresh tokens, `require_owner`/`require_admin`-style guard dependencies (`get_current_user`, `require_admin`), `/v1/auth/*` endpoints, first Alembic migration. Added `RefreshToken` table (§5) and the test-isolation pattern (§11) not anticipated in the original plan.
- **2026-08-19** — Initial backend plan created from `Requirement.md`.
