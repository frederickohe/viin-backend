from typing import Optional

from pydantic import BaseModel, Field

class NLURequest(BaseModel):
    phone: Optional[str] = Field(
        None,
        min_length=10,
        max_length=15,
        description="Optional phone override; authenticated users use their account phone",
    )
    message: str = Field(..., min_length=1, max_length=1000, description="User message to process")
    
    class Config:
        json_schema_extra = {
            "example": {
                "phone": "0234567890",
                "message": "I want to send 50 cedis to 0234567890"
            }
        }