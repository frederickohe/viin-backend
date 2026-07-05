from typing import Any, Dict, List

from pydantic import BaseModel


class UserAgentsResponse(BaseModel):
    agents: Dict[str, Any]
    available_agents: List[str]
