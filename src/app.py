import os
import sys

from another_fastapi_jwt_auth import AuthJWT
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic_settings import BaseSettings

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exceptions
from routes import base_routes
from core.auth.controller.authcontroller import auth_routes
from core.user.controller.usercontroller import user_routes
from core.cloudstorage.controller.storagecontoller import storage_routes
from core.notification.controller.notificationcontroller import notification_routes
from core.otp.controller.otpcontroller import otp_routes
from core.subscription.controller.subscription_controller import subscription_routes
from core.webhooks.controller.webhookscontroller import webhooks_routes
from core.webhooks.controller.telegram_controller import telegram_routes
from core.nlu.controller.nlucontroller import nlu_routes
from core.paystack.controller.paystack_controller import paystack_routes
from core.agent.controller.agentcontroller import agent_routes
from core.media.controller.media_controller import media_routes
from core.conversationmanager.controller.conversation_controller import conversation_routes
from core.memory.controller.memory_controller import memory_routes
from core.integrations.controller.google_calendar_controller import google_calendar_routes

from utilities.dbconfig import Base, engine
from config import settings
from utilities.exceptions import DatabaseValidationError
from fastapi.exceptions import RequestValidationError
from loguru import logger
from contextlib import asynccontextmanager


# Initialize FastAPI with lifespan event handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown"""
    # Startup
    logger.info("[APP_STARTUP] Application starting...")
    try:
        import utilities.dbmodels  # noqa: F401 — register all ORM models
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        logger.warning(f"[APP_STARTUP] Database init skipped: {e}")

    # Start memory scheduler on one worker only (see gunicorn.conf.py post_fork).
    scheduler_enabled = os.environ.get("MEMORY_SCHEDULER_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if scheduler_enabled:
        try:
            from core.memory.service.memory_scheduler_service import MemorySchedulerService

            MemorySchedulerService().start()
        except Exception as e:
            logger.warning(f"[APP_STARTUP] Memory scheduler init skipped: {e}")
    else:
        logger.info("[APP_STARTUP] Memory scheduler disabled for this worker")

    # Register Telegram webhook when configured
    try:
        from core.webhooks.service.telegram_service import TelegramService

        telegram = TelegramService()
        auto_set = os.environ.get("TELEGRAM_AUTO_SET_WEBHOOK", "true").lower() == "true"
        if auto_set and telegram.is_configured and telegram.webhook_url:
            if telegram.set_webhook():
                info = telegram.get_webhook_info() or {}
                logger.info(
                    "[APP_STARTUP] Telegram webhook active: url=%s pending=%s",
                    info.get("url"),
                    info.get("pending_update_count"),
                )
            else:
                logger.warning("[APP_STARTUP] Telegram webhook registration failed")
        elif auto_set and telegram.is_configured:
            logger.warning(
                "[APP_STARTUP] TELEGRAM_BOT_TOKEN set but TELEGRAM_WEBHOOK_URL missing; "
                "skipping webhook registration"
            )
        if telegram.is_configured:
            if telegram.set_my_commands():
                logger.info("[APP_STARTUP] Telegram bot command menu registered")
            else:
                logger.warning("[APP_STARTUP] Telegram bot command menu registration failed")
    except Exception as e:
        logger.warning(f"[APP_STARTUP] Telegram webhook init skipped: {e}")
    yield
    # Shutdown
    logger.info("[APP_SHUTDOWN] Application shutting down...")
    try:
        from core.memory.service.memory_scheduler_service import MemorySchedulerService

        MemorySchedulerService.shutdown()
    except Exception as e:
        logger.error(f"[APP_SHUTDOWN_ERROR] Error shutting down memory scheduler: {str(e)}")


app = FastAPI(
    title=settings.SERVICE_NAME,
    version="1.0",
    description="""**Autobus Core API** Backend services for the Autobus assistant platform.

    Default Endpoints:
    - Authentication
    - File and Document Management
    - Message and Task Queuing
    - Notifications
    """,
    contact={
        "name": "API Support",
        "url": "https://useautobus.com",
        "email": "support@useautobus.com",
    },
    license_info={
        "name": "MIT",
    },
    lifespan=lifespan
)


@app.get("/health")
async def root_health():
    """Docker / load-balancer probe."""
    return {"status": "healthy", "service": settings.SERVICE_NAME}


@app.get("/", include_in_schema=False)
async def api_root():
    return RedirectResponse(url="/docs")


@app.get("/swagger", include_in_schema=False)
async def swagger_redirect():
    return RedirectResponse(url="/docs")


# -----------------------------------------------------------
# Middleware (CORS)
# -----------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers

app.add_exception_handler(DatabaseValidationError, exceptions.database_validation_exception_handler)
app.add_exception_handler(RequestValidationError, exceptions.validation_exception_handler)

# Routes Registration

app.include_router(base_routes, prefix="/api/v1", tags=["Base Routes"])
app.include_router(storage_routes, prefix="/api/v1/storage", tags=["Storage Routes"])
app.include_router(auth_routes, prefix="/api/v1/auth", tags=["Auth Routes"])
app.include_router(user_routes, prefix="/api/v1/user", tags=["User Routes"])
app.include_router(notification_routes, prefix="/api/v1/notification", tags=["Notification Routes"])
app.include_router(otp_routes, prefix="/api/v1/otp", tags=["OTP Routes"])
app.include_router(subscription_routes, prefix="/api/v1/subscription", tags=["Subscription Routes"])
app.include_router(webhooks_routes, prefix="/api/v1/webhooks", tags=["Webhooks Routes"])
app.include_router(telegram_routes, prefix="/api/v1/webhooks", tags=["Webhooks Routes"])
app.include_router(nlu_routes, prefix="/api/v1/nlu", tags=["NLU Routes"])
app.include_router(paystack_routes, prefix="/api/v1/paystack", tags=["Paystack Routes"])
app.include_router(agent_routes, prefix="/api/v1/agent", tags=["Agent Routes"])
app.include_router(media_routes, prefix="/api/v1/media", tags=["Media Generation"])
app.include_router(conversation_routes, prefix="/api/v1/conversations", tags=["Conversation Routes"])
app.include_router(memory_routes, prefix="/api/v1/memory", tags=["Memory Routes"])
app.include_router(
    google_calendar_routes,
    prefix="/api/v1/integrations/google-calendar",
    tags=["Google Calendar Integration"],
)

# JWT Authentication Settings
class JWTSettings(BaseSettings):
    authjwt_secret_key: str = settings.SECRET_KEY
    authjwt_algorithm: str = settings.ALGORITHM
    authjwt_access_token_expires: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60  # in seconds
    authjwt_refresh_token_expires: int = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60  # in seconds


@AuthJWT.load_config
def get_config():
    return JWTSettings()