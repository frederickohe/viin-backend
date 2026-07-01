from typing import Optional

from pydantic import BaseModel, Field, model_validator

from core.auth.dto.request.password_policy import PASSWORD_MIN_LENGTH


class UserLoginRequest(BaseModel):
    email: Optional[str] = Field(default=None, min_length=1, max_length=255)
    username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=100)

    @model_validator(mode="after")
    def require_email_or_username(self):
        if not self.email and not self.username:
            raise ValueError("Email or username is required")
        return self

    @property
    def login_identifier(self) -> str:
        return (self.email or self.username or "").strip()
