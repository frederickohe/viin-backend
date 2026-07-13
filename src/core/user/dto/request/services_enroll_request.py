from typing import List

from pydantic import BaseModel, field_validator

from core.user.product_services import VALID_SERVICES, normalize_services


class ServicesEnrollRequest(BaseModel):
    services: List[str]

    @field_validator("services")
    @classmethod
    def validate_services(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("At least one service is required")
        for raw in value:
            if str(raw).strip().lower() not in VALID_SERVICES:
                raise ValueError(f"Unsupported service: {raw}")
        return normalize_services(value)
