from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import URL
import os
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra fields from .env that aren't defined in the model
    )
    
    SERVICE_NAME: str = "Viin Backend"
    DEBUG: bool = True

    # Database Configuration - supports both traditional and Docker Postgres env vars
    DB_DRIVER: str = os.environ.get('DB_DRIVER', 'postgresql+asyncpg')
    DB_HOST: Optional[str] = os.environ.get('PGHOST') or os.environ.get('DB_HOST')
    DB_PORT: int = int(os.environ.get('PGPORT', os.environ.get('DB_PORT', 5432)))
    DB_USER: Optional[str] = os.environ.get('PGUSER') or os.environ.get('DB_USER')
    DB_PASSWORD: Optional[str] = os.environ.get('PGPASSWORD') or os.environ.get('DB_PASSWORD')
    DB_DATABASE: Optional[str] = os.environ.get('PGDATABASE') or os.environ.get('DB_DATABASE')
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 0
    DB_ECHO: bool = os.environ.get('DB_ECHO', 'false').lower() == 'true'

    # JWT Configuration
    SECRET_KEY: str = os.environ.get('SECRET_KEY', os.environ.get('JWT_SECRET_KEY', 'green-secret-keeps-gamma'))
    ALGORITHM: str = os.environ.get('ALGORITHM', os.environ.get('JWT_ALGORITHM', 'HS256'))
    KID: str = os.environ.get('KID', 'viin-kid')
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 360
    
    # Redis Configuration
    REDIS_HOST: str = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT: str = os.environ.get('REDIS_PORT', '6379')
    REDIS_PASSWORD: str = os.environ.get('REDIS_PASSWORD', '')
    
    # Message Queue Configuration
    RABBIT_MQ_URL: str = os.environ.get('RABBIT_MQ_URL', '')
    RABBIT_MQ_ROUTING_KEY: str = os.environ.get('RABBIT_MQ_ROUTING_KEY', '')
    RABBIT_MQ_AUDIT_QUEUE: str = os.environ.get('RABBIT_MQ_AUDIT_QUEUE', '')
    SMS_MQ_QUEUE: str = os.environ.get('SMS_MQ_QUEUE', '')
    EMAIL_MQ_QUEUE: str = os.environ.get('EMAIL_MQ_QUEUE', '')
    BASE_FRONTEND_URL: str = os.environ.get('BASE_FRONTEND_URL', 'http://localhost:3000')
    BATCH_CUSTOMER_UPLOAD_QUEUE: str = os.environ.get('BATCH_CUSTOMER_UPLOAD_QUEUE', '')
    COMPANY_QUEUE: str = os.environ.get('COMPANY_QUEUE', '')

    # Wirepick SMS Configuration
    # Note: some env files include accidental surrounding quotes; we normalize via .strip().
    WIREPICK_API_URL: str = os.environ.get("WIREPICK_API_URL", "https://api.wirepick.com/httpsms").strip().strip('"').strip("'")
    WIREPICK_CLIENT_ID: str = os.environ.get("WIREPICK_CLIENT_ID", "").strip()
    WIREPICK_PASSWORD: str = os.environ.get("WIREPICK_PASSWORD", "").strip()
    WIREPICK_PUBLIC_KEY: str = os.environ.get("WIREPICK_PUBLIC_KEY", "").strip()
    WIREPICK_SENDER_ID: str = os.environ.get("WIREPICK_SENDER_ID", "Viin").strip()
    USE_WIREPICK_API_KEY: bool = os.environ.get("USE_WIREPICK_API_KEY", "false").lower() == "true"

    # Email (SMTP) configuration (used for OTP email + agent email tool)
    ZEPTOMAIL_SMTP_HOST: str = os.environ.get("ZEPTOMAIL_SMTP_HOST", "smtp.zeptomail.com").strip()
    ZEPTOMAIL_SMTP_PORT: int = int(os.environ.get("ZEPTOMAIL_SMTP_PORT", 587))
    ZEPTOMAIL_SMTP_USERNAME: str = os.environ.get("ZEPTOMAIL_SMTP_USERNAME", "emailapikey").strip()
    # Some envs store Zeptomail SMTP password as API token.
    ZEPTOMAIL_SMTP_PASSWORD: str = (
        os.environ.get("ZEPTOMAIL_SMTP_PASSWORD")
        or os.environ.get("ZEPTOMAIL_API_TOKEN")
        or ""
    ).strip()
    ZEPTOMAIL_FROM_EMAIL: str = os.environ.get("ZEPTOMAIL_FROM_EMAIL", "").strip()
    # OTP Configuration
    # Default to 30 seconds (can override via env).
    OTP_EXPIRE_SECONDS: int = int(os.environ.get("OTP_EXPIRE_SECONDS", 30))
    # Backward-compatible minutes value for any legacy call sites.
    OTP_EXPIRE_MINUTES: float = OTP_EXPIRE_SECONDS / 60

    # MongoDB Logging
    MONGO_URI: str = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
    MONGO_DB_NAME: str = os.environ.get('MONGO_DB_NAME', 'api_logs_db')
    
    # Logging levels
    LOG_LEVEL: str = os.environ.get('LOG_LEVEL', 'INFO')

    # Comma-separated users.id values that receive admin inbox notifications
    ADMIN_NOTIFICATION_USER_IDS: str = os.environ.get("ADMIN_NOTIFICATION_USER_IDS", "")
    SMS_NOTIFICATION_ENABLED: bool = os.environ.get("SMS_NOTIFICATION_ENABLED", "true").lower() == "true"

    # Paystack (standalone billing checkout)
    PAYSTACK_SECRET_KEY: str = os.environ.get("PAYSTACK_SECRET_KEY", "").strip()
    PAYSTACK_BILLING_CALLBACK_URL: str = os.environ.get("PAYSTACK_BILLING_CALLBACK_URL", "").strip()

    @property
    def DB_DSN(self) -> URL:
        return URL.create(
            self.DB_DRIVER,
            self.DB_USER,
            self.DB_PASSWORD,
            self.DB_HOST,
            self.DB_PORT,
            self.DB_DATABASE,
        )

    @property
    def DB_URL_STRING(self) -> str:
        return f'{self.DB_DRIVER}://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}?async_fallback=true'

    def MULTI_TENANT_DB_STRING(self, migration_id: str) -> str:
        return (f'jdbc:postgresql://{self.DB_HOST}:'
                f'{self.DB_PORT}/{migration_id}?ApplicationName=MultiTenant')


settings = Settings()