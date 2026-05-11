from pydantic import BaseModel, EmailStr, Field
from core.auth.dto.request.password_policy import PASSWORD_MIN_LENGTH

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    reset_token: str
    new_password: str = Field(..., min_length=PASSWORD_MIN_LENGTH)
