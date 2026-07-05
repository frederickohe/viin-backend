import logging
from typing import Optional

from sqlalchemy.orm import Session

from core.nlu.config import SYSTEM_PROMPTS
from core.nlu.service.llmclient import LLMClient

logger = logging.getLogger(__name__)

MARKETING_AGENT_NAMES = frozenset({
    "marketing",
    "digital_marketing",
    "digital-marketing",
    "digital_margeting",
})


class AutoBus:
    def __init__(self, db_session: Optional[Session] = None):
        """Lightweight agent facade; routes marketing prompts to LLM, everything else to NLU."""
        self.db_session = db_session
        self._llm_client: Optional[LLMClient] = None
        logger.info("AutoBus initialized")

    @property
    def llm_client(self) -> LLMClient:
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client

    def process_user_message(
        self,
        userid: str,
        message: str,
        agent_name: str,
        db_session: Optional[Session] = None,
    ) -> str:
        """Process a user message, optionally targeting a specific agent."""
        try:
            logger.info(
                "Received message from %s (agent=%s): %s",
                userid,
                agent_name,
                (message or "")[:200],
            )

            if self._is_marketing_agent(agent_name):
                return self._generate_marketing_content(message)

            from core.nlu.nlu import AutobusNLUSystem

            session = db_session or self.db_session
            nlu = AutobusNLUSystem(db_session=session)
            return nlu.process_message(userid, message).text

        except Exception as e:
            logger.error("Error processing message for user %s: %s", userid, e, exc_info=True)
            return "Sorry, I could not process your message. Please try again."

    @staticmethod
    def _is_marketing_agent(agent_name: str) -> bool:
        return (agent_name or "").strip().lower() in MARKETING_AGENT_NAMES

    def _generate_marketing_content(self, prompt: str) -> str:
        """Generate ad/social copy for digital marketing without NLU intent detection."""
        user_message = (prompt or "").strip()
        if not user_message:
            return "Please provide a short description of what you want to promote."

        response = self.llm_client.chat_completion(
            system_prompt=SYSTEM_PROMPTS["marketing"],
            user_message=user_message,
            conversation_history=None,
            temperature=0.8,
            max_tokens=800,
        )
        if not response:
            return "Sorry, I could not generate marketing text right now. Please try again."
        return response
