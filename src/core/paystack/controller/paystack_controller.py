from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
import secrets
import string

from core.user.controller.usercontroller import validate_token, get_db
from another_fastapi_jwt_auth import AuthJWT
from utilities.dbconfig import SessionLocal
from core.paystack.dto.request.paystack_request import PaystackInitializeRequest
from core.paystack.dto.response.paystack_response import PaystackInitializeResponse, PaystackVerifyResponse
from core.paystack.service.paystack_service import PaystackService

paystack_routes = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@paystack_routes.post("/transaction/initialize", response_model=PaystackInitializeResponse)
async def initialize_paystack_transaction(
    request: PaystackInitializeRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Initialize a Paystack transaction.
    Returns an authorization_url for the user to complete payment in their browser.
    """
    # Get current user from JWT
    current_user_email = authjwt.get_jwt_subject()
    
    # You might want to get the user_id from database using email
    from core.user.service.user_service import UserService
    user_service = UserService(db)
    user = user_service.get_current_user(current_user_email)
    
    # Initialize Paystack service
    paystack_service = PaystackService(db)
    
    # Initialize transaction
    response = await paystack_service.initialize_transaction(
        user_id=user.id,
        request=request
    )
    
    return response

@paystack_routes.get("/transaction/verify/{reference}", response_model=PaystackVerifyResponse)
async def verify_paystack_transaction(
    reference: str,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Verify a Paystack transaction after payment is completed
    """
    paystack_service = PaystackService(db)
    response = await paystack_service.verify_transaction(reference)
    return response

@paystack_routes.get("/transaction/banks")
async def get_paystack_banks(
    country: str = Query("nigeria", description="Country code (e.g., nigeria, ghana)"),
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db)
):
    """
    Get list of banks from Paystack
    """
    paystack_service = PaystackService(db)
    banks = await paystack_service.list_banks(country)
    return {"status": True, "data": banks}