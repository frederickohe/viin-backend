import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.conversationmanager.dto.conversation_response_dto import (
    ConversationDetailDTO,
    ConversationSummaryDTO,
)
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

    def _conversation_row_access_filter(self, user_identifier: str):
        """Match rows owned by phone/id variants or scoped ``<merchant_id>:<customer>`` sessions."""
        user_ids = self._conversation_user_ids(user_identifier)
        if not user_ids:
            return None
        resolved = self._resolve_user_db_id(user_identifier)
        parts = [DailyConversation.user_id.in_(user_ids)]
        if resolved:
            parts.append(DailyConversation.user_id.like(f"{resolved}:%"))
        return or_(*parts) if len(parts) > 1 else parts[0]

    def list_grouped_conversations_for_user(
        self,
        user_identifier: str,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[ConversationSummaryDTO], List[ConversationSummaryDTO]]:
        access = self._conversation_row_access_filter(user_identifier)
        if access is None:
            return [], []

        completed_rows = self._query_completed(access, skip=skip, limit=limit).all()

        user_keys = {row.user_id for row in completed_rows}
        user_names, user_phones = self._load_user_display_fields(user_keys)

        completed = [self._to_summary(row, user_names, user_phones) for row in completed_rows]
        return completed, []

    def _query_completed(self, access_filter, skip: int, limit: int):
        """Sessions for history / all-chats list."""
        return (
            self.db.query(DailyConversation)
            .filter(access_filter)
            .order_by(DailyConversation.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )

    def _load_user_display_fields(
        self, user_ids: set
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Resolve customer fullnames and canonical phone numbers by conversation user_id keys."""
        if not user_ids:
            return {}, {}
        lookup_keys: set = set()
        for uid in user_ids:
            if not uid:
                continue
            lookup_keys.add(uid)
            s = str(uid)
            if ":" in s:
                left, _, right = s.partition(":")
                lookup_keys.add(left.strip())
                lookup_keys.add(right.strip())
            normalized = self._normalize_phone_like(s)
            if normalized:
                lookup_keys.add(normalized)
        users = (
            self.db.query(User)
            .filter(
                or_(User.phone.in_(list(lookup_keys)), User.id.in_(list(user_ids)))
            )
            .all()
        )
        names: Dict[str, str] = {}
        phones: Dict[str, str] = {}
        for user in users:
            names[user.id] = user.fullname
            if user.phone:
                canonical = (user.phone or "").strip()
                if canonical:
                    names[user.phone] = user.fullname
                    phones[user.id] = canonical
                    phones[canonical] = canonical
                    normalized = self._normalize_phone_like(canonical)
                    if normalized and normalized != canonical:
                        phones[normalized] = canonical
        for uid in user_ids:
            if not uid or uid in phones:
                continue
            nu = self._normalize_phone_like(str(uid))
            if nu and nu in phones:
                phones[str(uid)] = phones[nu]
        return names, phones

    def _load_user_fullnames(self, user_ids: set) -> Dict[str, str]:
        names, _ = self._load_user_display_fields(user_ids)
        return names

    def _to_summary(
        self,
        row: DailyConversation,
        user_names: Dict[str, str],
        user_phones: Dict[str, str],
    ) -> ConversationSummaryDTO:
        state = row.conversation_state or {}
        history = state.get("conversation_history") or []
        last_message = self._last_customer_message(history)

        return ConversationSummaryDTO(
            id=row.id,
            conversation_id=state.get("conversation_id"),
            user_id=row.user_id,
            user_fullname=user_names.get(self._customer_channel_id(row.user_id))
            or user_names.get(row.user_id),
            conversation_date=row.conversation_date,
            conversation_lifecycle=state.get("conversation_lifecycle", "active"),
            intervention_active=bool(state.get("intervention_active", False)),
            intervention_id=state.get("intervention_id"),
            intervention_reason=state.get("intervention_reason"),
            current_intent=state.get("current_intent") or None,
            last_message=last_message,
            customer_phone=self._resolve_customer_phone(row.user_id, user_phones),
            message_count=len(history),
            created_at=row.created_at or datetime.utcnow(),
            updated_at=row.updated_at or datetime.utcnow(),
        )

    @staticmethod
    def _customer_channel_id(conversation_user_id: Optional[str]) -> str:
        uid = (conversation_user_id or "").strip()
        if ":" in uid:
            return uid.split(":", 1)[-1].strip()
        return uid

    def _resolve_customer_phone(
        self, conversation_user_id: Optional[str], user_phones: Dict[str, str]
    ) -> Optional[str]:
        uid = self._customer_channel_id(conversation_user_id)
        if not uid:
            return None
        candidates = {uid}
        normalized = self._normalize_phone_like(uid)
        if normalized:
            candidates.add(normalized)
        for key in candidates:
            if key in user_phones:
                return user_phones[key]
        digits = "".join(ch for ch in uid if ch.isdigit())
        if len(digits) >= 9:
            return uid
        return None

    @staticmethod
    def _last_customer_message(history: list) -> Optional[str]:
        """Latest message from the customer (role user), not bot or human agent."""
        if not history:
            return None
        for entry in reversed(history):
            role = (entry.get("role") or "").strip().lower()
            if role not in ("user", "customer"):
                continue
            content = entry.get("content")
            if content is None and entry.get("text") is not None:
                content = entry.get("text")
            if content is None:
                continue
            text = str(content)
            return text[:500] if len(text) > 500 else text
        return None

    def _session_owned_by_user(self, session_id: int, user_identifier: str) -> Optional[DailyConversation]:
        access = self._conversation_row_access_filter(user_identifier)
        if access is None:
            return None
        return (
            self.db.query(DailyConversation)
            .filter(
                DailyConversation.id == int(session_id),
                access,
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

    def _to_detail(
        self, row: DailyConversation, user_names: Dict[str, str]
    ) -> ConversationDetailDTO:
        state = row.conversation_state or {}
        history = state.get("conversation_history") or []
        return ConversationDetailDTO(
            id=row.id,
            conversation_id=state.get("conversation_id"),
            user_id=row.user_id,
            user_fullname=user_names.get(self._customer_channel_id(row.user_id))
            or user_names.get(row.user_id),
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
