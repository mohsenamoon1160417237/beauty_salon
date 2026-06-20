from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, RedisDsn, field_validator
from typing import Optional


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Salon Booking Service"
    APP_ENV: str = "production"
    DEBUG: bool = False
    SECRET_KEY: str
    ALLOWED_HOSTS: list[str] = ["*"]

    # Database
    DATABASE_URL: PostgresDsn
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    # Redis
    REDIS_URL: RedisDsn
    REDIS_LOCK_TTL: int = 30  # seconds

    # Cal.com (Cal DIY)
    CALCOM_BASE_URL: str
    CALCOM_API_KEY: str
    CALCOM_WEBHOOK_SECRET: str

    # WhatsApp (Meta Cloud API)
    WHATSAPP_PHONE_NUMBER_ID: str
    WHATSAPP_ACCESS_TOKEN: str
    WHATSAPP_VERIFY_TOKEN: str
    WHATSAPP_BUSINESS_ACCOUNT_ID: Optional[str] = None

    # Twilio (fallback)
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_WHATSAPP_FROM: Optional[str] = None

    # LLM
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    LLM_PROVIDER: str = "anthropic"  # openai | anthropic
    LLM_MODEL: str = "claude-sonnet-4-6"
    LLM_MAX_TOKENS: int = 2048
    LLM_TEMPERATURE: float = 0.3

    # n8n
    N8N_WEBHOOK_BASE_URL: Optional[str] = None
    N8N_API_KEY: Optional[str] = None

    # Scheduling
    DEFAULT_TIMEZONE: str = "America/New_York"
    BOOKING_WINDOW_DAYS: int = 60
    MIN_BOOKING_ADVANCE_HOURS: int = 2
    MAX_CONCURRENT_BOOKINGS_PER_STAFF: int = 1

    # Multi-tenant
    TENANT_ID: Optional[str] = None  # null = single-salon mode

    # Observability
    SENTRY_DSN: Optional[str] = None
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
