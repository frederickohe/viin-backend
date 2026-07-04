from typing import Any, Dict, Optional
import json
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from core.agent.tools.base_tool import BaseTool
import logging

from core.agent.tools.agent_config.user_agent_config_service import AgentConfigService

logger = logging.getLogger(__name__)


def _sanitize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize parameters by stripping whitespace from string values.
    
    This fixes issues where LLMs might introduce unintended spaces or newlines
    in generated values (e.g., "noreply@useviin. com" instead of "noreply@useviin.com").
    
    Args:
        params: Dictionary of parameters to sanitize
        
    Returns:
        Dictionary with whitespace stripped from string values
    """
    sanitized = {}
    for key, value in params.items():
        if isinstance(value, str):
            sanitized[key] = value.strip()
        else:
            sanitized[key] = value
    return sanitized


class UpdateAgentToolInput(BaseModel):
    """Input schema for UpdateAgentTool"""
    user_id: str = Field(..., description="The unique identifier of the user")
    agent_name: str = Field(..., description="The name of the agent to update")
    params: Dict[str, Any] = Field(..., description="Dictionary of parameters to update")
    status: Optional[str] = Field(default=None, description="Update the agent status ('active' or 'inactive')")


class UpdateAgentTool(BaseTool):
    """LangChain tool for updating an agent configuration."""
    
    name: str = "user_agent_config_update_tool"
    description: str = (
        "Update parameters for an existing agent configuration. "
        "Modifies agent settings, parameters, or status. "
        "Use this to adjust agent behavior or add/update configuration values."
    )
    args_schema: type[BaseModel] = UpdateAgentToolInput
    
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

    def _run(
        self,
        user_id: str,
        agent_name: str,
        params: Dict[str, Any],
        status: Optional[str] = None
    ) -> str:
        """Update agent configuration.
        
        Args:
            user_id: The ID of the user
            agent_name: Name of the agent to update
            params: Dictionary of parameters to update
            status: Optional new status for the agent
            
        Returns:
            JSON string with success/error information
        """
        if not self.service:
            return json.dumps({"ok": False, "message": "Database session not initialized"})
        
        try:
            # Sanitize parameters to remove unintended whitespace
            params = _sanitize_params(params)
            
            metadata = {}
            if status:
                metadata["status"] = status.strip() if isinstance(status, str) else status
                
            result = self.service.create_or_update_agent(
                user_id=user_id,
                agent_name=agent_name,
                params=params,
                **metadata
            )
            
            if result.get("ok"):
                return json.dumps({"ok": True, "message": f"Agent {agent_name} updated successfully", "agent": result.get("agent")})
            else:
                return json.dumps({"ok": False, "message": result.get("message")})
        except Exception as e:
            logger.error(f"Error updating agent: {e}", exc_info=True)
            return json.dumps({"ok": False, "message": f"Error updating agent: {str(e)}"})

    async def _arun(
        self,
        user_id: str,
        agent_name: str,
        params: Dict[str, Any],
        status: Optional[str] = None
    ) -> str:
        """Async version of _run"""
        return self._run(user_id, agent_name, params, status)
    
    # Legacy method for backward compatibility
    def forward(
        self,
        user_id: str,
        agent_name: str,
        params: Dict[str, Any],
        status: Optional[str] = None
    ) -> str:
        """Legacy forward method for backward compatibility"""
        return self._run(user_id, agent_name, params, status)