from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AgentUpdateRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)
    status: Optional[str] = None
