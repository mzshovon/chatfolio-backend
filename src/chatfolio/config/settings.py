from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    GEMINI = "gemini"
    CLAUDE = "claude"
    GROK = "grok"
    OPENROUTER = "openrouter"


class LLMTask(StrEnum):
    EXTRACTION = "extraction"
    GENERATION = "generation"
    INTENT = "intent"
    CHAT = "chat"
    EMBEDDING = "embedding"


class _Base(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class DatabaseSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="DATABASE_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    user: str = "chatfolio"
    password: SecretStr = SecretStr("chatfolio")
    name: str = "chatfolio"

    @property
    def async_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class RedisSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class StorageSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="STORAGE_", env_file=".env", extra="ignore")

    provider: Literal["s3", "minio"] = "minio"
    endpoint_url: str | None = "http://localhost:9000"
    bucket: str = "chatfolio-cv"
    region: str = "us-east-1"
    access_key: SecretStr = SecretStr("minioadmin")
    secret_key: SecretStr = SecretStr("minioadmin")
    max_cv_upload_mb: int = 20


class SecuritySettings(_Base):
    model_config = SettingsConfigDict(env_prefix="SECURITY_", env_file=".env", extra="ignore")

    # >=32 bytes so local/test runs don't trip PyJWT's InsecureKeyLengthWarning on every request —
    # still an obvious placeholder. create_app() refuses to start with this value outside `local`.
    jwt_secret: SecretStr = SecretStr("change-me-in-env-generate-a-real-random-secret-please")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    cors_origins: list[str] = ["http://localhost:3000"]
    password_reset_token_ttl_minutes: int = 30
    email_change_token_ttl_minutes: int = 30
    two_factor_challenge_ttl_minutes: int = 5
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    google_oauth_state_ttl_minutes: int = 10
    # Fernet key for encrypting third-party secrets at rest (Google refresh/access tokens) —
    # unlike jwt_secret this must be reversible, since the backend has to present the plaintext
    # token back to Google later. Generate with `Fernet.generate_key()`; create_app() refuses to
    # start with this placeholder outside `local`, same guard as jwt_secret.
    token_encryption_key: SecretStr = SecretStr(
        "change-me-in-env-generate-with-fernet-generate-key"
    )


class AppSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    # Used only to build links in outbound emails (e.g. the reset-password link) — the backend
    # never redirects here itself.
    frontend_base_url: str = "http://localhost:3000"


class EmailSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="EMAIL_", env_file=".env", extra="ignore")

    # smtp_host left unset by default: EmailSender logs-and-skips instead of sending when unset,
    # so local dev/tests never need a real mailbox. Set every SMTP_* var to actually deliver mail.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = True
    from_address: str = "no-reply@chatfolio.example.com"


class SmsSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="SMS_", env_file=".env", extra="ignore")

    # Any of these left unset = VendorSmsSender logs and skips instead of sending, same as email.
    api_url: str | None = None
    client_id: str | None = None
    client_password: SecretStr | None = None


class VectorStoreSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="VECTORSTORE_", env_file=".env", extra="ignore")

    provider: Literal["chroma"] = "chroma"
    host: str = "localhost"
    port: int = 8001


class LLMSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    default_provider: LLMProviderName = LLMProviderName.DEEPSEEK
    provider_for_extraction: LLMProviderName | None = None
    provider_for_generation: LLMProviderName | None = None
    provider_for_intent: LLMProviderName | None = None
    provider_for_chat: LLMProviderName | None = None
    provider_for_embedding: LLMProviderName | None = None

    deepseek_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    grok_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    # OpenAI-compatible `/chat/completions` roots, one per implemented provider. Overridable so
    # a proxy/gateway, a regional endpoint or a mock server can be pointed at without a code
    # change. A trailing slash is optional.
    deepseek_base_url: str = "https://api.deepseek.com"
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Model used per provider when it is the env-selected one (LLM_DEFAULT_PROVIDER /
    # LLM_PROVIDER_FOR_*): INTENT, EXTRACTION, callers without an owner — and the system default
    # every user falls back to. User-chosen models (premium) come from the DB instead.
    # No default for OpenAI/Gemini: model ids age quickly, so you set the one you want.
    deepseek_model: str | None = "deepseek-chat"
    openai_model: str | None = None
    gemini_model: str | None = None

    # Empirically tuned, not guessed: local all-MiniLM-L6-v2 cosine similarity between a natural
    # recruiter question and a terse resume-style chunk ("Skill: PHP, category: ...") lands
    # ~0.26-0.5 for genuine matches (0.7+ only on near-exact keyword overlap) versus ~0.07-0.16
    # for genuinely unrelated questions ("what's your favorite pizza topping") — a clear gap,
    # confirmed against real profile data on 2026-08-21. 0.55 was filtering out almost every real
    # match, not just irrelevant ones (that incident also involved a distance-metric bug in
    # chroma_store.py that made retrieval fail even more broadly). The CHAT_SYSTEM_PROMPT_TEMPLATE's
    # "use ONLY the context below, admit gaps honestly" instruction is the second guardrail layer,
    # so this threshold doesn't need to carry the entire anti-hallucination burden alone.
    retrieval_similarity_threshold: float = 0.25

    # None (the default) loads the bundled chat_prompts.toml next to llm/prompts/chat.py. Set
    # LLM_PROMPTS_CONFIG_PATH to an absolute path (e.g. one mounted into the container from
    # outside the image) to tune the chat assistant's tone/rules without a code change or
    # rebuild — see that file's own header comment for the format.
    prompts_config_path: str | None = None

    def api_key_for(self, provider: LLMProviderName) -> str | None:
        """The configured API key for a provider, or None if unset/blank."""
        secret = {
            LLMProviderName.DEEPSEEK: self.deepseek_api_key,
            LLMProviderName.OPENAI: self.openai_api_key,
            LLMProviderName.GEMINI: self.gemini_api_key,
            LLMProviderName.CLAUDE: self.anthropic_api_key,
            LLMProviderName.GROK: self.grok_api_key,
            LLMProviderName.OPENROUTER: self.openrouter_api_key,
        }[provider]
        value = secret.get_secret_value() if secret is not None else ""
        return value or None

    def model_for(self, provider: LLMProviderName) -> str | None:
        """The configured model for an env-selected provider, or None if unset/blank."""
        value = {
            LLMProviderName.DEEPSEEK: self.deepseek_model,
            LLMProviderName.OPENAI: self.openai_model,
            LLMProviderName.GEMINI: self.gemini_model,
        }.get(provider)
        return value.strip() if value and value.strip() else None

    def base_url_for(self, provider: LLMProviderName) -> str | None:
        """The configured API root for providers that have an adapter, else None."""
        return {
            LLMProviderName.DEEPSEEK: self.deepseek_base_url,
            LLMProviderName.OPENAI: self.openai_base_url,
            LLMProviderName.GEMINI: self.gemini_base_url,
        }.get(provider)

    def provider_for(self, task: LLMTask) -> LLMProviderName:
        override = {
            LLMTask.EXTRACTION: self.provider_for_extraction,
            LLMTask.GENERATION: self.provider_for_generation,
            LLMTask.INTENT: self.provider_for_intent,
            LLMTask.CHAT: self.provider_for_chat,
            LLMTask.EMBEDDING: self.provider_for_embedding,
        }[task]
        return override or self.default_provider


class GoogleOAuthSettings(_Base):
    model_config = SettingsConfigDict(env_prefix="GOOGLE_", env_file=".env", extra="ignore")

    client_id: str | None = None
    client_secret: SecretStr | None = None
    # Must byte-for-byte match a "Authorized redirect URI" registered in the Google Cloud
    # Console for this client — Google rejects the exchange otherwise. Points at this backend
    # directly (Google redirects the browser here after consent), not at the frontend.
    redirect_uri: str = "http://localhost:8000/api/v1/meeting-settings/google/callback"
    scopes: list[str] = [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ]
    authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url: str = "https://oauth2.googleapis.com/token"
    revoke_url: str = "https://oauth2.googleapis.com/revoke"
    userinfo_url: str = "https://www.googleapis.com/oauth2/v2/userinfo"
    calendar_events_url: str = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class SupportKoriSettings(_Base):
    """Support Kori sells Chatfolio and settles payment itself; the buyer gets a license key and we
    validate it against their API. A valid license = payment done."""

    model_config = SettingsConfigDict(env_prefix="SUPPORT_KORI_", env_file=".env", extra="ignore")

    # The product id registered with Support Kori. Required for activation; a plan can override it
    # with its own `external_product_ref` (e.g. one Kori product per duration).
    product_id: str | None = None
    verify_url: str = "https://supportkori.com/api/licenses/verify"
    timeout_seconds: float = 10.0
    # A license is re-checked with Kori at most this often, to catch refunds/revocations.
    revalidate_after_days: int = 7
    # Wrong/unknown keys a single account may submit per hour before activation is paused for it
    # (stops key-guessing; successful activations never count).
    max_failed_attempts_per_hour: int = 10


class FeatureFlags(_Base):
    model_config = SettingsConfigDict(env_prefix="FEATURE_", env_file=".env", extra="ignore")

    enable_custom_domains: bool = False
    enable_billing: bool = False


class ObservabilitySettings(_Base):
    model_config = SettingsConfigDict(env_prefix="OBSERVABILITY_", env_file=".env", extra="ignore")

    # None (the default) means Sentry stays off — no DSN, no network calls, no-op init. Same
    # opt-in shape as everything else in this class: safe to leave unset in local/test, wired up
    # by setting the env var in staging/production.
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    metrics_enabled: bool = True


class Settings(_Base):
    env: Environment = Environment.LOCAL
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    storage: StorageSettings = StorageSettings()
    security: SecuritySettings = SecuritySettings()
    vectorstore: VectorStoreSettings = VectorStoreSettings()
    llm: LLMSettings = LLMSettings()
    google_oauth: GoogleOAuthSettings = GoogleOAuthSettings()
    features: FeatureFlags = FeatureFlags()
    observability: ObservabilitySettings = ObservabilitySettings()
    app: AppSettings = AppSettings()
    email: EmailSettings = EmailSettings()
    sms: SmsSettings = SmsSettings()
    support_kori: SupportKoriSettings = SupportKoriSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
