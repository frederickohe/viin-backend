from fastapi import FastAPI
from another_fastapi_jwt_auth import AuthJWT
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exceptions
from routes import base_routes
from core.auth.controller.authcontroller import auth_routes
from core.user.controller.usercontroller import user_routes
from core.cloudstorage.controller.storagecontoller import storage_routes
from core.notification.controller.notificationcontroller import notification_routes
from core.payments.controller.billcontroller import bill_routes
from core.payments.controller.invoicecontroller import invoice_routes
from core.payments.controller.paymentcontroller import payment_routes
from core.otp.controller.otpcontroller import otp_routes
from core.subscription.controller.subscription_controller import subscription_routes
from core.webhooks.controller.webhookscontroller import webhooks_routes
from core.customers.controller.customer_controller import customer_routes
from core.nlu.controller.nlucontroller import nlu_routes
from core.paystack.controller.paystack_controller import paystack_routes
from core.agent.controller.agentcontroller import agent_routes
from core.socialmedia.controller.socialmedia_controller import social_routes
from core.chatwoot.controller.chatwoot_controller import chatwoot_routes
from core.integrations.controller.integration_test_controller import integration_routes
from core.product.controller.product_controller import product_routes
from core.orders.controller.order_controller import order_routes
from core.interventions.controller.intervention_controller import intervention_routes

from utilities.dbconfig import Base, engine
from config import settings
from utilities.exceptions import DatabaseValidationError
from fastapi.exceptions import RequestValidationError
from sqlalchemy import inspect

from loguru import logger
from contextlib import asynccontextmanager


# Initialize FastAPI with lifespan event handler
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown"""
    # Startup
    logger.info("[APP_STARTUP] Application starting...")
    yield
    # Shutdown
    logger.info("[APP_SHUTDOWN] Application shutting down...")
    try:
        from core.payments.service.payment_check_service import PaymentCheckService
        PaymentCheckService.shutdown_scheduler()
    except Exception as e:
        logger.error(f"[APP_SHUTDOWN_ERROR] Error shutting down scheduler: {str(e)}")


app = FastAPI(
    title=settings.SERVICE_NAME,
    version="1.0",
    description="""**Autobus Core API** An AI focused app infrastructure deployed with python.

    Default Endpoints:
    - Authentication
    - File and Document Management
    - Message and Task Queuing
    - Notifications
    """,
    contact={
        "name": "API Support",
        "url": "http://support@autobus.com",
        "email": "mail@autobus.com",
    },
    license_info={
        "name": "MIT",
    },
    lifespan=lifespan
)

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
app.include_router(payment_routes, prefix="/api/v1/payment", tags=["Payment Routes"])
app.include_router(bill_routes, prefix="/api/v1/bill", tags=["Billing Routes"])
app.include_router(invoice_routes, prefix="/api/v1/invoice", tags=["Invoice Routes"])
app.include_router(otp_routes, prefix="/api/v1/otp", tags=["OTP Routes"])
app.include_router(subscription_routes, prefix="/api/v1/subscription", tags=["Subscription Routes"])
app.include_router(customer_routes, prefix="/api/v1/customers", tags=["Customer Routes"])
app.include_router(webhooks_routes, prefix="/api/v1/webhooks", tags=["Webhooks Routes"])
app.include_router(nlu_routes, prefix="/api/v1/nlu", tags=["NLU Routes"])
app.include_router(paystack_routes, prefix="/api/v1/paystack", tags=["Paystack Routes"])
app.include_router(agent_routes, prefix="/api/v1/agent", tags=["Agent Routes"])
app.include_router(social_routes, prefix="/api/v1/social", tags=["Social Media Routes"])
app.include_router(chatwoot_routes, prefix="/api/v1/chatwoot", tags=["Chatwoot Routes"])
app.include_router(integration_routes, prefix="/api/v1/integrations", tags=["Integrations (self-test)"])
app.include_router(product_routes, prefix="/api/v1/products", tags=["Product Routes"])
app.include_router(order_routes, prefix="/api/v1/orders", tags=["Order Routes"])
app.include_router(intervention_routes, prefix="/api/v1/interventions", tags=["Interventions Routes"])

# JWT Authentication Settings
class JWTSettings(BaseSettings):
    authjwt_secret_key: str = settings.SECRET_KEY
    authjwt_algorithm: str = settings.ALGORITHM
    authjwt_access_token_expires: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60  # in seconds
    authjwt_refresh_token_expires: int = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60  # in seconds


@AuthJWT.load_config
def get_config():
    return JWTSettings()