from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from core.auth.dto.request.password_policy import PASSWORD_MIN_LENGTH
from core.user.product_services import VALID_SERVICES, normalize_services

class UserCreateRequest(BaseModel):  
    fullname: str
    email: str
    phone: Optional[str] = None
    profile_picture_url: Optional[str] = None
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH)
    # Product enrollment — same signup endpoint, different services (assistant / trading)
    services: Optional[List[str]] = None

    @field_validator("services")
    @classmethod
    def validate_services(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        for raw in value:
            if str(raw).strip().lower() not in VALID_SERVICES:
                raise ValueError(f"Unsupported service: {raw}")
        return normalize_services(value)
    
    # Personal Information
    nationality: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[str] = None
    location: Optional[str] = None
    ghana_card: Optional[str] = None
    
    # Membership Information
    company: Optional[str] = None
    current_branch: Optional[str] = None
    staff_id: Optional[str] = None
    
    # Connection Information
    facebook_url: Optional[str] = None
    whatsapp_number: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    instagram_url: Optional[str] = None
    
    # Notification Preferences
    profile_sharing: Optional[bool] = None
    in_app_notification: Optional[bool] = None
    sms_notification: Optional[bool] = None
