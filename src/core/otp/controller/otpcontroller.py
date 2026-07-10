from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from utilities.dbconfig import get_db
from core.otp.service.otpservice import OTPService
from core.otp.dto.request.otp_send_request import OTPSendRequest
from core.otp.dto.request.otp_verify_request import OTPVerifyRequest
from core.otp.dto.response.otp_send_response import OTPSendResponse
from core.otp.dto.response.otp_verify_response import OTPVerifyResponse
from core.otp.dto.request.otp_test import OTPTest

otp_routes = APIRouter()


@otp_routes.post("/send", response_model=OTPSendResponse)
def send_otp(request: OTPSendRequest, db: Session = Depends(get_db)):
    """Send OTP to phone or email"""
    otp_service = OTPService(db)
    
    if not request.phone and not request.email:
        raise HTTPException(status_code=400, detail="Either phone or email must be provided")
    
    if request.phone and request.email:
        raise HTTPException(status_code=400, detail="Provide either phone or email, not both")
    
    if request.phone:
        result = otp_service.send_otp_phone(request.phone)
    else:
        result = otp_service.send_otp_email(request.email)
    
    if not result.success:
        raise HTTPException(status_code=500, detail=result.message)
    
    return result


@otp_routes.post("/verify", response_model=OTPVerifyResponse)
def verify_otp(request: OTPVerifyRequest, db: Session = Depends(get_db)):
    """Verify OTP for phone or email"""
    otp_service = OTPService(db)
    
    if not request.phone and not request.email:
        raise HTTPException(status_code=400, detail="Either phone or email must be provided")
    
    if request.phone and request.email:
        raise HTTPException(status_code=400, detail="Provide either phone or email, not both")
    
    is_valid = otp_service.validate_otp(
        phone=request.phone,
        email=request.email,
        otp=request.otp
    )
    
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    
    return {
        "success": True,
        "message": "OTP verified successfully"
    }

# Add to your controller for testing

@otp_routes.post("/test-sms")
def test_sms(request: OTPTest, db: Session = Depends(get_db)):
    """Test endpoint to debug SMS sending"""
    from core.sms.service.sms_factory import get_sms_service
    
    sms_service = get_sms_service()
    result = sms_service.send_sms(request.phone, "Test message from debug endpoint")
    
    return {
        "phone": request.phone,
        "result": result
    }