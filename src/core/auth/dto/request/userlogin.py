from pydantic import BaseModel, EmailStr, Field
from core.auth.dto.request.password_policy import PASSWORD_MIN_LENGTH

class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=100)
