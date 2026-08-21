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

    def provider_for(self, task: LLMTask) -> LLMProviderName:
        override = {
            LLMTask.EXTRACTION: self.provider_for_extraction,
            LLMTask.GENERATION: self.provider_for_generation,
            LLMTask.INTENT: self.provider_for_intent,
            LLMTask.CHAT: self.provider_for_chat,
            LLMTask.EMBEDDING: self.provider_for_embedding,
        }[task]
        return override or self.default_provider


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
    features: FeatureFlags = FeatureFlags()
    observability: ObservabilitySettings = ObservabilitySettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
