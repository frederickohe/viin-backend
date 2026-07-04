# core/rag/user_data_manager.py
from typing import Dict, List, Any


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
        core_bio = self._extract_core_bio(full_user_data)
        return {"user_bio": core_bio}

    def _extract_core_bio(self, user_data: Dict) -> Dict:
        return {
            "user_id": user_data.get("user_id"),
            "username": user_data.get("username"),
            "email": user_data.get("email"),
            "first_name": user_data.get("first_name"),
            "last_name": user_data.get("last_name"),
            "is_active": user_data.get("is_active"),
            "member_since": user_data.get("created_at"),
            "location": "Ghana",
        }

    def _get_transaction_history(self, user_id: str, intent: str, slots: Dict) -> List[Dict]:
        return []
