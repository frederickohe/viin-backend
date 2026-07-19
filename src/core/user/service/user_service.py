
from fastapi import APIRouter, Depends, HTTPException, status, Query
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from core.auth.service.sessiondriver import SessionDriver, TokenData
from another_fastapi_jwt_auth import AuthJWT
from core.exceptions import *
from utilities.dbconfig import SessionLocal
from sqlalchemy import or_
from sqlalchemy.orm import Session
from core.user.model.User import User
from utilities.phone_utils import convert_to_local_ghana_format, normalize_ghana_phone_number
import logging
import re

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# DTO Models
from core.user.dto.request.user_filter_request import UserFilterRequest
from core.user.dto.response.message_response import MessageResponse
from core.user.dto.response.user_response import UserResponse
from core.user.dto.request.user_update_request import UserUpdateRequest
from core.user.product_services import merge_services, normalize_services, services_from_user

# Service Class
class UserService:
    def __init__(self, db: Session):
        self.db = db

    def _to_response(self, user: User) -> UserResponse:
        return UserResponse(
            id=user.id,
            fullname=user.fullname,
            email=user.email,
            phone=user.phone,
            nationality=user.nationality,
            date_of_birth=user.date_of_birth,
            gender=user.gender,
            address=user.address,
            location=user.location,
            ghana_card=user.ghana_card,
            profile_picture_url=user.profile_picture_url,
            company=user.company,
            current_branch=user.current_branch,
            staff_id=user.staff_id,
            occupation=user.occupation,
            organization_workplace=user.organization_workplace,
            facebook_url=user.facebook_url,
            whatsapp_number=user.whatsapp_number,
            linkedin_url=user.linkedin_url,
            twitter_url=user.twitter_url,
            instagram_url=user.instagram_url,
            profile_sharing=user.profile_sharing,
            in_app_notification=user.in_app_notification,
            sms_notification=user.sms_notification,
            services=services_from_user(user),
            enabled=user.enabled,
            status=user.status,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    def get_current_user(self, identifier: str) -> UserResponse:
        # Try to find by email first, then by id as a fallback.
        user = self.db.query(User).filter(User.email == identifier).first()
        if not user:
            user = self.db.query(User).filter(User.id == identifier).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self._to_response(user)

    def get_user_by_id(self, user_id: str) -> UserResponse:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self._to_response(user)

    def find_user_by_phone(self, phone: str) -> Optional[User]:
        """Resolve a user by phone or WhatsApp id, trying common Ghana formats."""
        if not phone or not str(phone).strip():
            return None

        candidates: list[str] = []
        seen: set[str] = set()
        for raw in (
            str(phone).strip(),
            normalize_ghana_phone_number(phone),
            convert_to_local_ghana_format(phone),
        ):
            if not raw:
                continue
            for variant in (raw, re.sub(r"\D", "", raw)):
                if not variant or variant in seen:
                    continue
                seen.add(variant)
                candidates.append(variant)
                if variant.startswith("233") and len(variant) == 12:
                    plus_variant = f"+{variant}"
                    if plus_variant not in seen:
                        seen.add(plus_variant)
                        candidates.append(plus_variant)

        for candidate in candidates:
            user = (
                self.db.query(User)
                .filter(or_(User.phone == candidate, User.whatsapp_number == candidate))
                .first()
            )
            if user:
                return user

        target = normalize_ghana_phone_number(phone)
        if not target:
            return None

        def national_digits(value: str) -> Optional[str]:
            digits = re.sub(r"\D", "", value or "")
            if digits.startswith("233") and len(digits) >= 12:
                return digits[-9:]
            if digits.startswith("0") and len(digits) >= 10:
                return digits[-9:]
            if len(digits) == 9:
                return digits
            return None

        target_national = national_digits(target) or national_digits(phone)

        rows = (
            self.db.query(User)
            .filter(or_(User.phone.isnot(None), User.whatsapp_number.isnot(None)))
            .all()
        )
        for user in rows:
            for field in (user.phone, user.whatsapp_number):
                if not field:
                    continue
                if normalize_ghana_phone_number(field) == target:
                    return user
                if target_national and national_digits(field) == target_national:
                    return user
        return None

    # get user by phone number
    def get_user_by_phone(self, phone: str) -> UserResponse:
        user = self.find_user_by_phone(phone)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self._to_response(user)
    
    def set_user_enabled_status(self, user_id: str, enabled: bool) -> MessageResponse:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.is_active = enabled
        self.db.commit()
        status_msg = "enabled" if enabled else "disabled"
        return MessageResponse(message=f"User {status_msg} successfully")

    def delete_user(self, user_id: str) -> MessageResponse:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        self.db.delete(user)
        self.db.commit()
        return MessageResponse(message="User deleted successfully")

    def get_all_users_paged(self, page: int, size: int):
        query = self.db.query(User)
        total = query.count()
        users = query.offset((page - 1) * size).limit(size).all()
        
        return {
            "total": total,
            "page": page,
            "size": size,
            "users": [self._to_response(user) for user in users]
        }

    def enroll_services(self, identifier: str, services: list[str]) -> UserResponse:
        user = self.db.query(User).filter(User.email == identifier).first()
        if not user:
            user = self.db.query(User).filter(User.id == identifier).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.services = merge_services(services_from_user(user), normalize_services(services))
        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return self._to_response(user)

    def update_user(self, email: str, payload: UserUpdateRequest) -> UserResponse:
            # log the update attempt
            logger.debug(f"Updating user {email} with data: {payload.model_dump(exclude_unset=True)}")
            user = self.db.query(User).filter(User.email == email).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            data = payload.model_dump(exclude_unset=True)
            for key, value in data.items():
                if hasattr(user, key):
                    setattr(user, key, value)

            user.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(user)
            return self.get_user_by_id(user.id)

    def update_current_user(self, email: str, payload: UserUpdateRequest) -> UserResponse:
            # Resolve like get_current_user — JWT subject may be email or user id.
            user = self.db.query(User).filter(User.email == email).first()
            if not user:
                user = self.db.query(User).filter(User.id == email).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            data = payload.model_dump(exclude_unset=True)
            for key, value in data.items():
                if hasattr(user, key):
                    setattr(user, key, value)

            user.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(user)
            return self.get_user_by_id(user.id)

    def update_current_user_notification_settings(
        self,
        identifier: str,
        *,
        in_app_notification: bool | None = None,
        sms_notification: bool | None = None,
    ) -> UserResponse:
        user = self.db.query(User).filter(User.email == identifier).first()
        if not user:
            user = self.db.query(User).filter(User.id == identifier).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if in_app_notification is not None:
            user.in_app_notification = in_app_notification
        if sms_notification is not None:
            user.sms_notification = sms_notification

        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return self.get_user_by_id(user.id)

    def update_current_user_profile_image(self, identifier: str, *, profile_picture_url: str) -> UserResponse:
        user = self.db.query(User).filter(User.email == identifier).first()
        if not user:
            user = self.db.query(User).filter(User.id == identifier).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.profile_picture_url = profile_picture_url
        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return self.get_user_by_id(user.id)

    def update_user_notification_settings(
        self,
        user_id: str,
        *,
        in_app_notification: bool | None = None,
        sms_notification: bool | None = None,
    ) -> UserResponse:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if in_app_notification is not None:
            user.in_app_notification = in_app_notification
        if sms_notification is not None:
            user.sms_notification = sms_notification

        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return self.get_user_by_id(user.id)

    def update_user_profile_image(self, user_id: str, *, profile_picture_url: str) -> UserResponse:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.profile_picture_url = profile_picture_url
        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return self.get_user_by_id(user.id)

    def _resolve_user_by_phone(self, user_id: str) -> User:
        """Resolve a user from their WhatsApp / conversation phone id."""
        user = self.db.query(User).filter(User.phone == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def _resolve_user_for_profile(self, user_id: str) -> User:
        """Resolve by internal user id first, then phone (chat / NLU callers)."""
        user = self.db.query(User).filter(User.id == user_id).first()
        if user:
            return user
        return self._resolve_user_by_phone(user_id)

    def update_user_details(self, user_id: str, update_data: dict) -> UserResponse:
        """Update profile fields for the user identified by id or phone."""
        user = self._resolve_user_for_profile(user_id)
        field_map = {
            "phone": "phone",
            "phone_number": "phone",
            "username": "fullname",
            "fullname": "fullname",
            "name": "fullname",
            "location": "location",
            "occupation": "occupation",
            "address": "address",
            "company": "company",
        }

        updated = False
        for key, value in (update_data or {}).items():
            if value is None:
                continue
            attr = field_map.get(key, key)
            if hasattr(user, attr):
                setattr(user, attr, value)
                updated = True

        if not updated:
            raise HTTPException(
                status_code=400,
                detail="No valid profile fields to update.",
            )

        user.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(user)
        return self.get_user_by_id(user.id)

    def get_user_profile(self, user_id: str) -> dict:
        """Return a profile summary dict for NLU display."""
        user = self._resolve_user_for_profile(user_id)
        email_agent = user.get_agent("email_agent") if user.agents else None
        sender_email = None
        if email_agent:
            sender_email = (email_agent.get("params") or {}).get("sender_email")

        return {
            "fullname": user.fullname,
            "username": user.fullname,
            "email": user.email,
            "phone": user.phone,
            "location": user.location,
            "occupation": user.occupation,
            "company": user.company,
            "sender_email": sender_email,
        }