from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, fields
import uuid

from utilities.dbconfig import SessionLocal

from core.nlu.model.Conversation import DailyConversation


@dataclass
class ConversationState:
    user_id: str
    conversation_id: str = ""
    conversation_lifecycle: str = "active"  # active | awaiting_followup_help | completed
    session_db_id: Optional[int] = None
    current_intent: str = ""
    collected_slots: Dict = None
    waiting_for_pin: bool = False
    pending_action: Dict = None
    conversation_history: List[Dict] = None
    conversation_date: date = None
    waiting_for_payment_confirmation: bool = False
    pending_payment_dto: Dict = None
    last_successful_transaction: Dict = None
    waiting_for_expense_date_selection: bool = False
    pending_expense_dates: List[Dict] = None
    intervention_active: bool = False
    intervention_id: Optional[int] = None
    intervention_trigger: Optional[str] = None
    intervention_reason: Optional[str] = None
    intervention_created_at: Optional[str] = None
    viin_linked_phone: Optional[str] = None
    viin_linked_user_id: Optional[str] = None

    def __post_init__(self):
        if not self.conversation_id:
            self.conversation_id = str(uuid.uuid4())
        if self.collected_slots is None:
            self.collected_slots = {}
        if self.conversation_history is None:
            self.conversation_history = []
        if self.conversation_date is None:
            self.conversation_date = date.today()
        if self.pending_payment_dto is None:
            self.pending_payment_dto = {}
        if self.last_successful_transaction is None:
            self.last_successful_transaction = {}
        if self.pending_expense_dates is None:
            self.pending_expense_dates = []

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "conversation_lifecycle": self.conversation_lifecycle,
            "session_db_id": self.session_db_id,
            "current_intent": self.current_intent,
            "collected_slots": self.collected_slots,
            "waiting_for_pin": self.waiting_for_pin,
            "pending_action": self.pending_action,
            "conversation_history": self.conversation_history,
            "conversation_date": self.conversation_date.isoformat(),
            "waiting_for_payment_confirmation": self.waiting_for_payment_confirmation,
            "pending_payment_dto": self.pending_payment_dto,
            "last_successful_transaction": self.last_successful_transaction,
            "waiting_for_expense_date_selection": self.waiting_for_expense_date_selection,
            "pending_expense_dates": self.pending_expense_dates,
            "intervention_active": self.intervention_active,
            "intervention_id": self.intervention_id,
            "intervention_trigger": self.intervention_trigger,
            "intervention_reason": self.intervention_reason,
            "intervention_created_at": self.intervention_created_at,
            "viin_linked_phone": self.viin_linked_phone,
            "viin_linked_user_id": self.viin_linked_user_id,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ConversationState":
        data = dict(data)
        if "conversation_date" in data and isinstance(data["conversation_date"], str):
            data["conversation_date"] = date.fromisoformat(data["conversation_date"])
        field_names = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in field_names}
        return cls(**filtered)


class ConversationManager:
    def __init__(self):
        self.db = SessionLocal()
        self.memory_cache: Dict[str, ConversationState] = {}

    def get_conversation_state(self, user_id: str) -> ConversationState:
        """Return the user's current (non-completed) conversation session, or start a new one."""
        if user_id in self.memory_cache:
            return self.memory_cache[user_id]

        loaded = self._load_active_session_from_db(user_id)
        if loaded:
            self.memory_cache[user_id] = loaded
            return loaded

        state = ConversationState(user_id=user_id, conversation_date=date.today())
        self.memory_cache[user_id] = state
        return state

    def _load_active_session_from_db(self, user_id: str) -> Optional[ConversationState]:
        rows = (
            self.db.query(DailyConversation)
            .filter(DailyConversation.user_id == user_id)
            .order_by(DailyConversation.updated_at.desc())
            .limit(40)
            .all()
        )
        for row in rows:
            raw = row.conversation_state or {}
            lifecycle = raw.get("conversation_lifecycle", "active")
            if lifecycle != "completed":
                merged = dict(raw)
                if not merged.get("conversation_id"):
                    merged["conversation_id"] = f"migrated-{row.id}"
                merged["session_db_id"] = row.id
                st = ConversationState.from_dict(merged)
                st.session_db_id = row.id
                return st
        return None

    def update_conversation_history(self, user_id: str, role: str, content: str):
        state = self.get_conversation_state(user_id)
        state.conversation_history.append(
            {"role": role, "content": content, "timestamp": datetime.utcnow().isoformat()}
        )
        if len(state.conversation_history) > 20:
            state.conversation_history = state.conversation_history[-20:]
        self._save_conversation_state(state)

    def _insert_row(self, state: ConversationState, payload: Dict):
        row = DailyConversation(
            user_id=state.user_id,
            conversation_date=state.conversation_date or date.today(),
            conversation_state=payload,
        )
        self.db.add(row)
        self.db.flush()
        self.db.refresh(row)
        state.session_db_id = row.id

    def _save_conversation_state(self, state: ConversationState):
        payload = state.to_dict()
        if state.session_db_id:
            row = (
                self.db.query(DailyConversation)
                .filter(DailyConversation.id == state.session_db_id)
                .first()
            )
            if row:
                row.conversation_state = payload
                row.conversation_date = state.conversation_date or date.today()
                row.updated_at = datetime.utcnow()
            else:
                self._insert_row(state, payload)
        else:
            self._insert_row(state, payload)

        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e

        self.memory_cache[state.user_id] = state

    def finalize_completed_session(self, user_id: str):
        """Mark the in-memory session completed, persist, and drop cache so the next turn starts fresh."""
        state = self.memory_cache.get(user_id) or self._load_active_session_from_db(user_id)
        if not state:
            return
        state.conversation_lifecycle = "completed"
        self._save_conversation_state(state)
        self.memory_cache.pop(user_id, None)

    def reset_conversation_state(self, user_id: str):
        """End the current session (same as abandoning / PIN error hard reset)."""
        self.finalize_completed_session(user_id)

    def clear_collected_slots(self, user_id: str):
        state = self.get_conversation_state(user_id)
        state.collected_slots = {}
        self._save_conversation_state(state)

    def set_pending_action(self, user_id: str, intent: str, slots: Dict):
        state = self.get_conversation_state(user_id)
        state.waiting_for_pin = True
        state.pending_action = {
            "intent": intent,
            "slots": slots,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_conversation_state(state)

    def get_previous_conversations(self, user_id: str, days_back: int = 7) -> List[ConversationState]:
        start_date = date.today() - timedelta(days=days_back)
        rows = (
            self.db.query(DailyConversation)
            .filter(
                DailyConversation.user_id == user_id,
                DailyConversation.conversation_date >= start_date,
            )
            .order_by(DailyConversation.updated_at.desc())
            .all()
        )
        return [ConversationState.from_dict(conv.conversation_state) for conv in rows]

    def cleanup_old_conversations(self, days_to_keep: int = 30):
        cutoff_date = date.today() - timedelta(days=days_to_keep)
        self.db.query(DailyConversation).filter(
            DailyConversation.conversation_date < cutoff_date
        ).delete()
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e
