from typing import Any, Dict, Optional
import json
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from core.agent.tools.base_tool import BaseTool
import logging

from core.agent.tools.agent_config.user_agent_config_service import AgentConfigService

logger = logging.getLogger(__name__)


class GetAgentToolInput(BaseModel):
    """Input schema for GetAgentTool"""
    user_id: str = Field(..., description="The unique identifier of the user")
    agent_name: str = Field(..., description="The name of the agent to retrieve")


class GetAgentTool(BaseTool):
    """LangChain tool for retrieving an agent configuration."""
    
    name: str = "user_agent_config_get_tool"
    description: str = (
        "Retrieve a specific agent configuration for a user. "
        "Returns the agent's parameters and metadata. "
        "Use this to check if an agent is configured or to review agent settings."
    )
    args_schema: type[BaseModel] = GetAgentToolInput
    
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

    def _run(self, user_id: str, agent_name: str) -> str:
        """Get a specific agent configuration.
        
        Args:
            user_id: The ID of the user
            agent_name: Name of the agent to retrieve
            
        Returns:
            JSON string with agent configuration or error message
        """
        if not self.service:
            return '{"ok": false, "message": "Database session not initialized"}'
        
        try:
            result = self.service.get_agent(user_id=user_id, agent_name=agent_name)
            
            if result.get("ok"):
                return json.dumps({"ok": True, "agent": result.get("agent")})
            else:
                return json.dumps({"ok": False, "message": result.get("message")})
        except Exception as e:
            logger.error(f"Error retrieving agent: {e}", exc_info=True)
            return json.dumps({"ok": False, "message": f"Error retrieving agent: {str(e)}"})

    async def _arun(self, user_id: str, agent_name: str) -> str:
        """Async version of _run"""
        return self._run(user_id, agent_name)
    
    # Legacy method for backward compatibility
    def forward(self, user_id: str, agent_name: str) -> str:
        """Legacy forward method for backward compatibility"""
        return self._run(user_id, agent_name)