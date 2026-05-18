import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.conversationmanager.dto.conversation_response_dto import (
    ConversationDetailDTO,
    ConversationSummaryDTO,
)
from core.interventions.model.Intervention import Intervention
from core.orders.model.order import Order
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
                DailyConversation.conversation_state["conversation_lifecycle"].as_string()
                == "completed",
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
                DailyConversation.conversation_state["intervention_active"].as_boolean().is_(True),
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

    def _session_owned_by_user(self, session_id: int, user_identifier: str) -> Optional[DailyConversation]:
        user_ids = self._conversation_user_ids(user_identifier)
        if not user_ids:
            return None
        return (
            self.db.query(DailyConversation)
            .filter(
                DailyConversation.id == int(session_id),
                DailyConversation.user_id.in_(user_ids),
            )
            .first()
        )

    def get_session_detail(
        self, user_identifier: str, session_id: int
    ) -> Optional[ConversationDetailDTO]:
        row = self._session_owned_by_user(session_id, user_identifier)
        if not row:
            return None
        user_names = self._load_user_fullnames({row.user_id})
        return self._to_detail(row, user_names)

    def get_conversation_for_order(
        self, merchant_user_id: str, order_id: str
    ) -> Optional[ConversationDetailDTO]:
        """Load the most recent conversation for the order's customer."""
        try:
            order_uuid = uuid.UUID(str(order_id))
        except ValueError:
            return None
        order = self.db.query(Order).filter(Order.order_id == order_uuid).first()
        if not order:
            return None
        if order.user_id and str(order.user_id) != str(merchant_user_id):
            return None

        customer_key = (order.customer_phone or order.customer_id or "").strip()
        if not customer_key:
            return None

        candidates = {customer_key}
        normalized = self._normalize_phone_like(customer_key)
        if normalized:
            candidates.add(normalized)

        row = (
            self.db.query(DailyConversation)
            .filter(DailyConversation.user_id.in_(list(candidates)))
            .order_by(DailyConversation.updated_at.desc())
            .first()
        )
        if not row:
            return None
        user_names = self._load_user_fullnames({row.user_id})
        return self._to_detail(row, user_names)

    def _to_detail(
        self, row: DailyConversation, user_names: Dict[str, str]
    ) -> ConversationDetailDTO:
        state = row.conversation_state or {}
        history = state.get("conversation_history") or []
        return ConversationDetailDTO(
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
            conversation_history=history,
            collected_slots=state.get("collected_slots"),
            created_at=row.created_at or datetime.utcnow(),
            updated_at=row.updated_at or datetime.utcnow(),
        )

    def append_human_message_to_session(
        self, user_identifier: str, session_id: int, message: str
    ) -> Optional[ConversationDetailDTO]:
        """Append an agent/human message to a session owned by the authenticated user."""
        row = self._session_owned_by_user(session_id, user_identifier)
        if not row:
            return None

        state = dict(row.conversation_state or {})
        if not state.get("intervention_active"):
            return None

        history = list(state.get("conversation_history") or [])
        history.append(
            {
                "role": "human",
                "content": message,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        if len(history) > 20:
            history = history[-20:]
        state["conversation_history"] = history
        row.conversation_state = state
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)

        user_names = self._load_user_fullnames({row.user_id})
        return self._to_detail(row, user_names)

    def deactivate_intervention_for_session(
        self, user_identifier: str, session_id: int
    ) -> Optional[ConversationDetailDTO]:
        """Turn off intervention mode for a stored conversation session."""
        row = self._session_owned_by_user(session_id, user_identifier)
        if not row:
            return None

        state = dict(row.conversation_state or {})
        intervention_id = state.get("intervention_id")
        if intervention_id is not None:
            intervention = (
                self.db.query(Intervention)
                .filter(Intervention.id == int(intervention_id))
                .first()
            )
            if intervention and intervention.status == "open":
                intervention.status = "closed"
                intervention.closed_at = datetime.utcnow()

        state["intervention_active"] = False
        state["intervention_id"] = None
        state["intervention_trigger"] = None
        state["intervention_reason"] = None
        row.conversation_state = state
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)

        user_names = self._load_user_fullnames({row.user_id})
        return self._to_detail(row, user_names)
