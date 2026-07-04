from typing import Optional
import json
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from core.agent.tools.base_tool import BaseTool
import logging

from core.agent.tools.agent_config.user_agent_config_service import AgentConfigService

logger = logging.getLogger(__name__)


class ListAgentsToolInput(BaseModel):
    """Input schema for ListAgentsTool"""
    user_id: str = Field(..., description="The unique identifier of the user")


class ListAgentsTool(BaseTool):
    """LangChain tool for listing all agent configurations for a user."""
    
    name: str = "user_agent_config_list_agents_tool"
    description: str = (
        "List all agent configurations for a user. "
        "Returns a dictionary of all configured agents with their settings. "
        "Use this to see what agents are available for the user."
    )
    args_schema: type[BaseModel] = ListAgentsToolInput
    
    db_session: Optional[Session] = None
    service: Optional[AgentConfigService] = None

    def __init__(self, db_session: Optional[Session] = None, **kwargs):
        """Initialize the tool with a database session.
        
        Args:
            db_session: SQLAlchemy database session for performing queries.
            **kwargs: Additional arguments for BaseTool
        """
        super().__init__(**kwargs)
        self.db_session = db_session
        self.service = AgentConfigService(db_session) if db_session else None

    def _run(self, user_id: str) -> str:
        """List all agent configurations for a user.
        
        Args:
            user_id: The ID of the user
            
        Returns:
            JSON string with list of agents or error message
        """
        if not self.service:
            return '{"ok": false, "message": "Database session not initialized"}'
        
        try:
            result = self.service.list_agents(user_id=user_id)
            
            if result.get("ok"):
                return json.dumps({"ok": True, "agents": result.get("agents")})
            else:
                return json.dumps({"ok": False, "message": result.get("message")})
        except Exception as e:
            logger.error(f"Error listing agents: {e}", exc_info=True)
            return json.dumps({"ok": False, "message": f"Error listing agents: {str(e)}"})

    async def _arun(self, user_id: str) -> str:
        """Async version of _run"""
        return self._run(user_id)
    
    # Legacy method for backward compatibility
    def forward(self, user_id: str) -> str:
        """Legacy forward method for backward compatibility"""
        return self._run(user_id)
