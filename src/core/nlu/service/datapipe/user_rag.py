# core/rag/user_data_manager.py
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class UserRAGManager:
    """Manages user data for RAG augmentation."""

    def __init__(self, max_context_tokens: int = 4000):
        self.max_context_tokens = max_context_tokens
        self.estimated_token_ratio = 4

    def get_extracted_user_context(
        self,
        user_id: str,
        intent: str,
        current_slots: Dict,
        full_user_data: Dict,
    ) -> Dict[str, Any]:
        return {"User Transaction History": self.get_transaction_history(user_id, intent, current_slots)}

    def get_transaction_history(self, user_id: str, intent: str, slots: Dict) -> List[Dict]:
        """Transaction history is unavailable after legacy payment module removal."""
        logger.debug(
            "[USER_RAG] Transaction history disabled for user_id=%s intent=%s",
            user_id,
            intent,
        )
        return []
