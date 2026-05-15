from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.conversationmanager.dto.conversation_response_dto import ConversationSummaryDTO
from core.nlu.model.Conversation import DailyConversation
from core.user.model.User import User


class ConversationListService:
    def __init__(self, db: Session):
        self.db = db

    def _normalize_phone_like(self, value: str) -> str:
        cleaned = "".join(ch for ch in (value or "") if ch.isdigit())
        if cleaned.startswith("233") and len(cleaned) > 3:
            cleaned = "0" + cleaned[3:]
        elif cleaned and not cleaned.startswith("0") and len(cleaned) == 9:
            cleaned = "0" + cleaned
        return cleaned

    def _resolve_user_db_id(self, user_identifier: str) -> Optional[str]:
        if not user_identifier:
            return None

        user = self.db.query(User).filter(User.id == user_identifier).first()
        if user:
            return user.id

        user = self.db.query(User).filter(User.email == user_identifier).first()
        if user:
            return user.id

        normalized_phone = self._normalize_phone_like(user_identifier)
        phone_candidates = {user_identifier}
        if normalized_phone:
            phone_candidates.add(normalized_phone)

        user = self.db.query(User).filter(User.phone.in_(list(phone_candidates))).first()
        return user.id if user else None

    def _conversation_user_ids(self, user_identifier: str) -> List[str]:
        """Identifiers that may appear as daily_conversations.user_id (id or phone)."""
        candidates = {user_identifier}
        resolved_id = self._resolve_user_db_id(user_identifier)
        if resolved_id:
            candidates.add(resolved_id)
            user = self.db.query(User).filter(User.id == resolved_id).first()
            if user and user.phone:
                candidates.add(user.phone)
                normalized = self._normalize_phone_like(user.phone)
                if normalized:
                    candidates.add(normalized)
        return [c for c in candidates if c]

    def list_grouped_conversations_for_user(
        self,
        user_identifier: str,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[ConversationSummaryDTO], List[ConversationSummaryDTO]]:
        user_ids = self._conversation_user_ids(user_identifier)
        if not user_ids:
            return [], []

        completed_rows = self._query_completed(user_ids, skip=skip, limit=limit).all()
        intervention_rows = self._query_intervention_active(
            user_ids, skip=skip, limit=limit
        ).all()

        user_names = self._load_user_fullnames(
            {row.user_id for row in completed_rows} | {row.user_id for row in intervention_rows}
        )

        completed = [self._to_summary(row, user_names) for row in completed_rows]
        intervention_active = [self._to_summary(row, user_names) for row in intervention_rows]
        return completed, intervention_active

    def _query_completed(self, user_ids: List[str], skip: int, limit: int):
        return (
            self.db.query(DailyConversation)
            .filter(
                DailyConversation.user_id.in_(user_ids),
                DailyConversation.conversation_state["conversation_lifecycle"].astext == "completed",
            )
            .order_by(DailyConversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )

    def _query_intervention_active(self, user_ids: List[str], skip: int, limit: int):
        return (
            self.db.query(DailyConversation)
            .filter(
                DailyConversation.user_id.in_(user_ids),
                DailyConversation.conversation_state["intervention_active"].astext == "true",
            )
            .order_by(DailyConversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )

    def _load_user_fullnames(self, user_ids: set) -> Dict[str, str]:
        if not user_ids:
            return {}
        users = (
            self.db.query(User)
            .filter(
                or_(User.phone.in_(list(user_ids)), User.id.in_(list(user_ids)))
            )
            .all()
        )
        names: Dict[str, str] = {}
        for user in users:
            if user.phone:
                names[user.phone] = user.fullname
            names[user.id] = user.fullname
        return names

    def _to_summary(
        self, row: DailyConversation, user_names: Dict[str, str]
    ) -> ConversationSummaryDTO:
        state = row.conversation_state or {}
        history = state.get("conversation_history") or []
        last_message = self._last_message(history)

        return ConversationSummaryDTO(
            id=row.id,
            conversation_id=state.get("conversation_id"),
            user_id=row.user_id,
            user_fullname=user_names.get(row.user_id),
            conversation_date=row.conversation_date,
            conversation_lifecycle=state.get("conversation_lifecycle", "active"),
            intervention_active=bool(state.get("intervention_active", False)),
            intervention_id=state.get("intervention_id"),
            intervention_reason=state.get("intervention_reason"),
            current_intent=state.get("current_intent") or None,
            last_message=last_message,
            message_count=len(history),
            created_at=row.created_at or datetime.utcnow(),
            updated_at=row.updated_at or datetime.utcnow(),
        )

    @staticmethod
    def _last_message(history: list) -> Optional[str]:
        if not history:
            return None
        content = history[-1].get("content")
        if content is None:
            return None
        text = str(content)
        return text[:500] if len(text) > 500 else text
